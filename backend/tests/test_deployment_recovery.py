"""Isolated loopback deployment and SQLite backup recovery tests."""
import json
import os
import socket
import sqlite3
import shutil
import hashlib
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lolarena.history_accounts import provision


def free_port():
    with closing(socket.socket()) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class DeploymentRecoveryTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(__file__).resolve().parents[1] / 'tmp'
        tmp.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=tmp)
        self.base = Path(self.temp.name)
        self.root = self.base / 'ledger'
        provision(self.root, ('test-agent',), track='online')
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self.temp.cleanup()

    def start(self, *args):
        process = subprocess.Popen([sys.executable, '-m', *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def wait(self, url):
        for _ in range(100):
            try:
                with urlopen(url, timeout=1) as response:
                    return response.status
            except HTTPError as exc:
                return exc.code
            except OSError:
                time.sleep(.1)
        self.fail('Service did not start')

    def test_control_and_bank_in_separate_loopback_processes(self):
        control_port = free_port()
        bank_port = free_port()
        self.start('lolarena.historical_control_api', '--root', str(self.root), '--port', str(control_port), '--online')
        self.start('lolarena.historical_bank', '--root', str(self.root), '--day-id', 'fictional', '--online',
                   '--allowed-host', f'127.0.0.1:{bank_port}', '--port', str(bank_port))
        self.assertEqual(self.wait(f'http://127.0.0.1:{bank_port}/healthz'), 200)
        url = f'http://127.0.0.1:{control_port}/v1/days/fictional/review-status'
        self.assertEqual(self.wait(url), 405)
        request = Request(url, data=b'{"match_id":"m"}', headers={'Content-Type':'application/json'}, method='POST')
        with self.assertRaises(HTTPError) as denied:
            urlopen(request, timeout=3)
        self.assertEqual(denied.exception.code, 401)
        token = (self.root / 'control-auth.token').read_text().strip()
        request.add_header('Authorization', 'Bearer ' + token)
        with self.assertRaises(HTTPError) as invalid:
            urlopen(request, timeout=3)
        self.assertEqual(invalid.exception.code, 400)

    def test_control_http_review_and_settlement(self):
        from test_historical_controller import HistoricalControllerTest
        fixture = HistoricalControllerTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.clock.value = datetime.now(timezone.utc)
        fixture.prepare()
        for account in fixture.controller.accounts:
            fixture.controller.open_first('day-1', account, 60)
            fixture.controller.finish_run('day-1', account, 'first', 'session', 'done')
            fixture.controller.open_recovery('day-1', account, 'session', 60)
            fixture.controller.finish_run('day-1', account, 'recovery', 'session', 'done')
        fixture.controller.seal_day('day-1')
        published = fixture.anchor + timedelta(hours=2)
        fixture.controller.advance_clock('day-1', published.isoformat())
        evidence = fixture.evidence(published)
        port = free_port()
        self.start('lolarena.historical_control_api', '--root', str(fixture.root), '--port', str(port))
        base = f'http://127.0.0.1:{port}/v1/days/day-1/'
        self.assertEqual(self.wait(base + 'review-status'), 405)
        token = (fixture.root / 'control-auth.token').read_text().strip()
        def post(operation, payload):
            request = Request(base + operation, data=json.dumps(payload).encode(),
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
            with urlopen(request, timeout=3) as response:
                return json.load(response)['result']
        payload = {'match_id':'fictional-1', 'score':'1-0', 'result_id':'result-1',
                   'source':'fixture', 'published_at':published.isoformat(), 'evidence':evidence}
        pending = post('prepare-settlement', payload)
        self.assertEqual(pending['status'], 'pending')
        confirmed = post('confirm-settlement', {'match_id':'fictional-1', 'sha256':pending['sha256']})
        self.assertFalse(confirmed['duplicate'])
        self.assertTrue(post('confirm-settlement', {'match_id':'fictional-1', 'sha256':pending['sha256']})['duplicate'])

    def test_frontend_serves_fictional_fixture(self):
        port = free_port()
        frontend = Path(__file__).resolve().parents[2] / 'frontend'
        self.start('http.server', str(port), '--bind', '127.0.0.1', '--directory', str(frontend))
        self.assertEqual(self.wait(f'http://127.0.0.1:{port}/'), 200)
        with urlopen(f'http://127.0.0.1:{port}/data/worlds-2026.json', timeout=3) as response:
            data = json.load(response)
        self.assertEqual(data['scheduleSource'], 'Fictional fixture')
        self.assertEqual(data['events'], [])

    def test_complete_ledger_root_backup_and_restore(self):
        original_token = (self.root / 'credentials/test-agent.token').read_text().strip()
        evidence = self.root / 'result-evidence'
        evidence.mkdir()
        (evidence / 'fictional.txt').write_text('fictional evidence')
        backup = self.base / 'root-backup'
        restored = self.base / 'root-restored'
        shutil.copytree(self.root, backup, symlinks=True)
        shutil.copytree(backup, restored, symlinks=True)
        def manifest(root):
            return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file()}
        self.assertEqual(manifest(self.root), manifest(restored))
        from lolarena.live_online_controller import LiveOnlineController
        controller = LiveOnlineController(restored)
        self.assertEqual(controller.authenticate(original_token), 'test-agent')
        with closing(sqlite3.connect(restored / 'bank/accounts.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual((restored / 'result-evidence/fictional.txt').read_text(), 'fictional evidence')

    def test_sqlite_backup_restore_on_independent_copy(self):
        source = self.root / 'bank/accounts.sqlite3'
        backup = self.base / 'backup.sqlite3'
        restored = self.base / 'restored.sqlite3'
        with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(backup)) as target:
            original.backup(target)
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("UPDATE accounts SET balance=13 WHERE account_id='test-agent'")
        with closing(sqlite3.connect(backup)) as saved, closing(sqlite3.connect(restored)) as target:
            saved.backup(target)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute("SELECT balance FROM accounts WHERE account_id='test-agent'").fetchone()[0], 1000)
        with closing(sqlite3.connect(source)) as db:
            self.assertEqual(db.execute("SELECT balance FROM accounts WHERE account_id='test-agent'").fetchone()[0], 13)
