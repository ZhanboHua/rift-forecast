"""Test orchestration with fictional adapters and no provider calls."""
import hashlib
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from lolarena.historical_controller import digest, encode
from lolarena.history_accounts import provision
from lolarena.live_online_controller import LiveOnlineController
from lolarena.orchestrator import run_day
import test_historical_controller as fixtures


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HistoricalControllerTest(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = Path(self.fixture.temporary.name) / 'orchestration'
        provision(self.root, ('model-a', 'model-b'), track='online')
        self.clock = fixtures.FakeClock()
        self.anchor = self.fixture.anchor
        self.clock.value = self.anchor - timedelta(minutes=301)
        self.controller = LiveOnlineController(self.root, self.clock)
        self.matches = [self.fixture.fixtures[0]]
        cutoff = self.anchor - timedelta(minutes=400)
        self.material = self.fixture.material
        self.attestation = {'state': 'VERIFIED_LIVE_SNAPSHOT', 'cutoff': cutoff.isoformat(),
                            'fixtures_sha256': digest(encode(self.matches)),
                            'files': [{'path': 'pack.md', 'visible_at': cutoff.isoformat(),
                                       'sha256': hashlib.sha256((self.material / 'pack.md').read_bytes()).hexdigest()}]}
        self.controller.prepare_day('fixture-day', self.anchor.isoformat(), self.matches[0]['announced_at'],
                                    self.matches, self.material, self.attestation)
        self.clock.value = self.anchor - timedelta(minutes=300)
        self.adapter = str(Path(__file__).parent / 'fixtures/mock_prediction_adapter.py')
        self.spec = {'command': [sys.executable, self.adapter], 'timeout_seconds': 30}

    def test_parallel_adapters_submit_to_independent_accounts(self):
        results = run_day(self.controller, 'fixture-day', {'model-a': self.spec, 'model-b': self.spec},
                          self.root / 'work', self.material, self.attestation)
        self.assertEqual([x['status'] for x in results], ['done', 'done'])
        with self.controller.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_runs WHERE first_status='done'").fetchone()[0], 2)
        with self.assertRaises(FileExistsError):
            run_day(self.controller, 'fixture-day', {'model-a': self.spec, 'model-b': self.spec},
                    self.root / 'work', self.material, self.attestation)

    def test_failed_adapter_is_unknown_and_does_not_submit(self):
        failed = {'command': [sys.executable, '-c', 'raise SystemExit(1)'], 'timeout_seconds': 30}
        results = run_day(self.controller, 'fixture-day', {'model-a': self.spec, 'model-b': failed},
                          self.root / 'work', self.material, self.attestation)
        self.assertEqual([x['status'] for x in results], ['done', 'U'])
        with self.controller.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_submissions WHERE account_id='model-b'").fetchone()[0], 0)

    def test_missing_credential_marks_unknown(self):
        (self.root / 'credentials' / 'model-b.token').unlink()
        results = run_day(self.controller, 'fixture-day', {'model-a': self.spec, 'model-b': self.spec},
                          self.root / 'work', self.material, self.attestation)
        self.assertEqual([x['status'] for x in results], ['done', 'U'])
        with self.controller.connect() as db:
            state = db.execute("SELECT first_status FROM historical_runs WHERE account_id='model-b'").fetchone()[0]
        self.assertEqual(state, 'U')

    def test_resume_mode_can_start_a_fresh_pending_day(self):
        results = run_day(self.controller, 'fixture-day', {'model-a': self.spec, 'model-b': self.spec},
                          self.root / 'new-work', self.material, self.attestation, resume=True)
        self.assertEqual([x['status'] for x in results], ['done', 'done'])

    def test_tampered_material_prevents_any_run(self):
        (self.material / 'pack.md').write_text('changed')
        with self.assertRaises(ValueError):
            run_day(self.controller, 'fixture-day', {'model-a': self.spec, 'model-b': self.spec},
                    self.root / 'work', self.material, self.attestation)
        with self.controller.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_runs WHERE first_status!='pending'").fetchone()[0], 0)

class CompatibleAdapterTest(unittest.TestCase):
    def test_two_models_use_local_mock_provider(self):
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from unittest.mock import patch

        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((data['model'], self.headers.get('Authorization')))
                content = json.dumps([{'match_id': 'fictional-1', 'p_team_a_win': 0.6,
                                       'winner_bet': {'team': 'AAA', 'stake': 0},
                                       'score_bets': {'1-0': 0, '0-1': 0}, 'rationale': 'fixture',
                                       'sources': [], 'take_loan': False}])
                body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        fixture = OrchestratorTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        adapter = str(Path(__file__).resolve().parents[2] / 'scripts/openai_compatible_adapter.py')
        url = f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
        def spec(model, key):
            return {'command': [sys.executable, adapter], 'timeout_seconds': 30,
                    'env_values': {'MODEL_NAME': model, 'MODEL_API_URL': url},
                    'env_map': {'MODEL_API_KEY': key}}
        with patch.dict(os.environ, {'FIXTURE_KEY_A': 'fixture-secret-a', 'FIXTURE_KEY_B': 'fixture-secret-b'}):
            results = run_day(fixture.controller, 'fixture-day',
                              {'model-a': spec('first', 'FIXTURE_KEY_A'), 'model-b': spec('second', 'FIXTURE_KEY_B')},
                              fixture.root / 'provider-work', fixture.material, fixture.attestation)
        self.assertEqual([r['status'] for r in results], ['done', 'done'])
        self.assertCountEqual(seen, [('first', 'Bearer fixture-secret-a'), ('second', 'Bearer fixture-secret-b')])

class CliIntegrationTest(unittest.TestCase):
    def test_preparation_and_orchestration_commands(self):
        import subprocess
        import tempfile
        from datetime import datetime, timezone
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            base = Path(temporary)
            ledger = base / 'online'
            provision(ledger, ('model-a', 'model-b'), track='online')
            anchor = datetime.now(timezone.utc) + timedelta(minutes=299, seconds=40)
            cutoff = anchor - timedelta(minutes=400)
            known = (cutoff - timedelta(minutes=1)).isoformat()
            fixtures_data = [{'match_id': 'cli-fixture', 'team_a': 'AAA', 'team_b': 'BBB', 'best_of': 1,
                              'start': anchor.isoformat(), 'announced_at': known}]
            materials = base / 'materials'
            materials.mkdir()
            (materials / 'pack.md').write_text('Fictional CLI material')
            manifest = {'day_id': 'cli-day', 'anchor': anchor.isoformat(), 'anchor_known_at': known, 'fixtures': fixtures_data}
            attestation = {'state': 'VERIFIED_LIVE_SNAPSHOT', 'cutoff': cutoff.isoformat(),
                           'fixtures_sha256': digest(encode(fixtures_data)),
                           'files': [{'path': 'pack.md', 'visible_at': known,
                                      'sha256': hashlib.sha256((materials / 'pack.md').read_bytes()).hexdigest()}]}
            adapter = str(Path(__file__).parent / 'fixtures/mock_prediction_adapter.py')
            config = {account: {'command': [sys.executable, adapter], 'timeout_seconds': 20} for account in ('model-a', 'model-b')}
            for name, data in [('manifest', manifest), ('attestation', attestation), ('config', config)]:
                (base / (name + '.json')).write_text(json.dumps(data))
            subprocess.run([sys.executable, '-m', 'lolarena.prepare_online_day', '--root', str(ledger),
                            '--manifest', str(base / 'manifest.json'), '--materials', str(materials),
                            '--attestation', str(base / 'attestation.json'), '--apply'], check=True, capture_output=True)
            result = subprocess.run([sys.executable, '-m', 'lolarena.orchestrator', '--root', str(ledger),
                                     '--day', 'cli-day', '--config', str(base / 'config.json'),
                                     '--workspaces', str(base / 'work'), '--materials', str(materials),
                                     '--attestation', str(base / 'attestation.json'), '--run'],
                                    check=True, capture_output=True, text=True)
            self.assertEqual([item['status'] for item in json.loads(result.stdout)], ['done', 'done'])
