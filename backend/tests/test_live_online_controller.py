"""Exercise the online control with a fictional, explicitly advanced clock."""
import hashlib
import json
import unittest
from datetime import timedelta
from pathlib import Path

from starlette.testclient import TestClient

from lolarena.historical_controller import HistoricalController, digest, encode
from lolarena.historical_control_api import create_app
from lolarena.history_accounts import provision
from lolarena.live_online_controller import LiveOnlineController
import test_historical_controller as fixtures


class OnlineTest(unittest.TestCase):
    def test_review_revision_and_wall_clock_gates(self):
        fixture_case = fixtures.HistoricalControllerTest()
        fixture_case.setUp()
        self.addCleanup(fixture_case.tearDown)
        root = Path(fixture_case.temporary.name) / 'online'
        provision(root)
        (root / 'bank/ONLINE_LEDGER').touch()
        clock = fixtures.FakeClock()
        anchor = fixture_case.anchor
        clock.value = anchor - timedelta(minutes=301)
        c = LiveOnlineController(root, clock)
        with self.assertRaises(ValueError):
            HistoricalController(root)
        f = fixture_case.fixtures[0]
        cutoff = anchor - timedelta(minutes=400)
        material = fixture_case.material
        att = {'state': 'VERIFIED_LIVE_SNAPSHOT', 'cutoff': cutoff.isoformat(),
               'fixtures_sha256': digest(encode([f])),
               'files': [{'path': 'pack.md', 'visible_at': cutoff.isoformat(),
                          'sha256': hashlib.sha256((material / 'pack.md').read_bytes()).hexdigest()}]}
        c.prepare_day('fictional-online', anchor.isoformat(), f['announced_at'], [f], material, att)
        d = 'fictional-online'
        tokens = {a: (root / 'credentials' / (a + '.token')).read_text().strip() for a in c.accounts}
        a = c.accounts[0]
        b = c.accounts[1]
        p = fixture_case.prediction(1, 1)
        revised = {**p, 'p_team_a_win': 0.7}
        with self.assertRaises(RuntimeError):
            c.open_first(d, a, 3600)
        clock.value = anchor - timedelta(minutes=300)
        for name in c.accounts:
            c.open_first(d, name, 3600)
            if name == a:
                c.submit(d, tokens[a], p)
                with self.assertRaises(RuntimeError):
                    c.submit(d, tokens[a], revised)
            if name == b:
                first_receipt = c.submit(d, tokens[b], p)
            c.finish_run(d, name, 'first', name, 'done')
        with self.assertRaises(RuntimeError):
            c.open_review(d, a, a)
        c.open_recovery(d, a, a, missing_summary=True)
        c.finish_run(d, a, 'recovery', a, 'done')
        clock.value = anchor - timedelta(minutes=180)
        with self.assertRaises(RuntimeError):
            c.open_review(d, a, 'different-session')
        c.open_review(d, a, a)
        c.submit(d, tokens[a], revised)
        c.finish_run(d, a, 'review', a, 'done')
        c.open_review(d, b, b)
        c.finish_run(d, b, 'review', b, 'U')
        with self.assertRaises(RuntimeError):
            c.open_review(d, b, a, retry=True)
        clock.value += timedelta(minutes=20)
        c.open_review(d, b, b, retry=True)
        c.submit(d, tokens[b], revised)
        c.finish_run(d, b, 'review', b, 'U')
        with self.assertRaises(RuntimeError):
            c.open_review(d, b, b, retry=True)
        with self.assertRaises(RuntimeError):
            c.seal_day(d)
        clock.value = anchor - timedelta(minutes=10)
        c.seal_day(d)
        with c.connect() as db:
            versions = db.execute('SELECT phase FROM online_submission_versions WHERE account_id=? ORDER BY version', (a,)).fetchall()
            self.assertEqual(versions, [('first',), ('review',)])
            retry = db.execute('SELECT ends_at,status FROM online_review_retries WHERE account_id=?', (b,)).fetchone()
            self.assertEqual(retry, ((anchor - timedelta(minutes=135)).isoformat(), 'U'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM online_submission_versions WHERE account_id=?', (b,)).fetchone(), (2,))
            body = db.execute('SELECT payload FROM historical_locks').fetchone()[0]
            self.assertEqual(json.loads(body)['entries'][a][f['match_id']]['prediction']['p_team_a_win'], 0.7)
            saved = json.loads(body)['entries'][b][f['match_id']]
            self.assertEqual(saved['status'], 'ok')
            self.assertEqual(saved['sha256'], first_receipt['matches'][f['match_id']]['sha256'])
        with self.assertRaises(ValueError):
            c.advance_clock(d, anchor.isoformat())
        with TestClient(create_app(c, 'fictional-control-token', {'testserver'})) as client:
            url = f'/v1/days/{d}/prepare-settlement'
            self.assertEqual(client.post(url, json={}).status_code, 401)
            client.headers['Authorization'] = 'Bearer fictional-control-token'
            published = anchor + timedelta(minutes=45)
            proof = fixture_case.evidence(published, f['match_id'], '1-0', 'result-online')
            (root / 'result-evidence').symlink_to(fixture_case.root / 'result-evidence', target_is_directory=True)
            result = {'match_id': f['match_id'], 'score': '1-0', 'result_id': 'result-online',
                      'source': 'fixture', 'published_at': published.isoformat(), 'evidence': proof}
            prepared = client.post(url, json=result).json()['result']
            confirm_url = f'/v1/days/{d}/confirm-settlement'
            confirmation = {'match_id': f['match_id'], 'sha256': prepared['sha256']}
            self.assertEqual(client.post(confirm_url, json=confirmation).status_code, 400)
            clock.value = published
            advance = client.post(f'/v1/days/{d}/advance', json={'value': published.isoformat()})
            self.assertEqual(advance.status_code, 200)
            self.assertEqual(client.post(confirm_url, json=confirmation).json()['result']['state'], 'settled')
            self.assertTrue(client.post(confirm_url, json=confirmation).json()['result']['duplicate'])
