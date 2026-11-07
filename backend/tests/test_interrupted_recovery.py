"""Verify restart recovery of an interrupted native supervisor with a fictional CLI."""
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lolarena.historical_controller import digest, encode
from lolarena.history_accounts import provision


class InterruptedRecoveryTest(unittest.TestCase):
    def test_supervisor_crash_resumes_recorded_native_session(self):
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            base = Path(temporary)
            ledger = base / 'online'
            provision(ledger, ('model-a',), track='online')
            anchor = datetime.now(timezone.utc) + timedelta(minutes=299, seconds=40)
            cutoff = anchor - timedelta(minutes=400)
            known = (cutoff - timedelta(minutes=1)).isoformat()
            fixtures = [{'match_id': 'fixture', 'team_a': 'AAA', 'team_b': 'BBB', 'best_of': 1,
                         'start': anchor.isoformat(), 'announced_at': known}]
            materials = base / 'materials'
            materials.mkdir()
            (materials / 'pack.md').write_text('Fictional test material')
            attestation = {'state': 'VERIFIED_LIVE_SNAPSHOT', 'cutoff': cutoff.isoformat(),
                           'fixtures_sha256': digest(encode(fixtures)),
                           'files': [{'path': 'pack.md', 'visible_at': known,
                                      'sha256': hashlib.sha256((materials / 'pack.md').read_bytes()).hexdigest()}]}
            manifest = {'day_id': 'fixture-day', 'anchor': anchor.isoformat(), 'anchor_known_at': known, 'fixtures': fixtures}
            adapter = str(Path(__file__).parent / 'fixtures/mock_codex_cli.py')
            config = {'model-a': {'protocol': 'codex', 'command': [sys.executable, adapter], 'model': 'fixture-model',
                                  'timeout_seconds': 30, 'max_recoveries': 1, 'env_values': {'HANG_FIRST': '1'}}}
            for name, data in [('manifest', manifest), ('attestation', attestation), ('config', config)]:
                (base / (name + '.json')).write_text(json.dumps(data))
            common = ['--root', str(ledger), '--materials', str(materials), '--attestation', str(base / 'attestation.json')]
            subprocess.run([sys.executable, '-m', 'lolarena.prepare_online_day', *common,
                            '--manifest', str(base / 'manifest.json'), '--apply'], check=True, capture_output=True)
            args = [sys.executable, '-m', 'lolarena.orchestrator', *common, '--day', 'fixture-day',
                    '--config', str(base / 'config.json'), '--workspaces', str(base / 'work'), '--run']
            supervisor = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                record = None
                for _ in range(200):
                    with sqlite3.connect(ledger / 'bank/accounts.sqlite3') as db:
                        try:
                            record = db.execute("SELECT session_id,pid FROM native_execution WHERE account_id='model-a'").fetchone()
                        except sqlite3.OperationalError:
                            record = None
                    if record and record[0] == 'fixture-session-123' and record[1]:
                        break
                    time.sleep(.05)
                self.assertIsNotNone(record)
                self.assertEqual(record[0], 'fixture-session-123')
                os.kill(supervisor.pid, signal.SIGKILL)
                supervisor.wait(timeout=10)
                resumed = subprocess.run([*args, '--resume-interrupted'], check=True, capture_output=True, text=True, timeout=40)
                output = json.loads(resumed.stdout)
                self.assertEqual(output[0]['status'], 'done')
                self.assertEqual(output[0]['session_id'], 'fixture-session-123')
                with sqlite3.connect(ledger / 'bank/accounts.sqlite3') as db:
                    self.assertEqual(db.execute('SELECT first_status,recovery_status FROM historical_runs').fetchone(), ('done', 'done'))
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0], 1)
            finally:
                if supervisor.poll() is None:
                    supervisor.terminate()
                    supervisor.wait(timeout=10)

class JournalSafetyTest(unittest.TestCase):
    def test_reused_pid_is_not_signalled(self):
        from unittest.mock import patch
        from lolarena import native_journal
        from test_orchestrator import OrchestratorTest
        fixture = OrchestratorTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        c = fixture.controller
        native_journal.ensure(c)
        workspace = fixture.root / 'journal-work'
        workspace.mkdir()
        spec = {'protocol': 'codex', 'command': ['fictional'], 'model': 'fixture'}
        native_journal.prepare(c, 'fixture-day', 'model-a', spec, workspace, 'first')
        with c.connect() as db:
            db.execute("UPDATE native_execution SET session_id=?,pid=?,boot_id=?,start_ticks=?,state='running' WHERE day_id=? AND account_id=?",
                       ('fixture-session', os.getpid(), native_journal.boot_id(), -1, 'fixture-day', 'model-a'))
        with patch('signal.pidfd_send_signal') as send:
            with self.assertRaises(RuntimeError):
                native_journal.recoverable(c, 'fixture-day', 'model-a', spec, workspace)
            send.assert_not_called()

    def test_missing_offline_wrapper_does_not_allow_duplicate_container(self):
        from unittest.mock import patch
        from lolarena import native_journal
        from test_orchestrator import OrchestratorTest
        fixture = OrchestratorTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.controller
        native_journal.ensure(controller)
        workspace = fixture.root / 'offline-journal-work'
        workspace.mkdir()
        spec = {'protocol': 'offline_codex', 'command': ['codex'], 'model': 'fixture'}
        native_journal.prepare(controller, 'fixture-day', 'model-a', spec, workspace, 'first')
        with controller.connect() as db:
            db.execute("UPDATE native_execution SET session_id=?,pid=?,boot_id=?,start_ticks=?,state='running' WHERE day_id=? AND account_id=?",
                       ('fixture-session', 9999999, native_journal.boot_id(), 1, 'fixture-day', 'model-a'))
        with patch('lolarena.native_journal.process_info', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Container cleanup is not verified'):
                native_journal.recoverable(controller, 'fixture-day', 'model-a', spec, workspace)
