"""Recover fictional offline containers after the supervisor is killed."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from test_historical_controller import HistoricalControllerTest


@unittest.skipUnless(os.environ.get('RIFT_OFFLINE_HANG_IMAGE'), 'requires a dedicated fictional CLI image')
class OfflineInterruptedRecoveryTest(unittest.TestCase):
    def test_recovers_owned_containers_and_preserves_sessions(self):
        fixture = HistoricalControllerTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.clock.value = datetime.now(timezone.utc)
        fixture.prepare()
        control_parent = Path(os.environ['RIFT_OFFLINE_CONTROL_PARENT'])
        with tempfile.TemporaryDirectory(prefix='rift-crash-', dir=control_parent) as temporary:
            base = Path(temporary)
            attestation = base / 'attestation.json'
            attestation.write_text(json.dumps(fixture.attestation))
            config = {account: {'protocol': 'offline_codex', 'command': ['codex'], 'model': 'fixture-model',
                                'timeout_seconds': 30, 'max_recoveries': 1,
                                'image': os.environ['RIFT_OFFLINE_HANG_IMAGE'],
                                'gateway_url': 'https://gateway.example/v1', 'control_dir': str(base),
                                'env_map': {'CODEX_API_KEY': 'FIXTURE_OFFLINE_KEY'}}
                      for account in fixture.controller.accounts}
            config_path = base / 'config.json'
            config_path.write_text(json.dumps(config))
            args = [sys.executable, '-m', 'lolarena.orchestrator', '--root', str(fixture.root), '--track', 'offline',
                    '--materials', str(fixture.material), '--attestation', str(attestation), '--day', 'day-1',
                    '--config', str(config_path), '--workspaces', str(base / 'work'), '--run']
            env = {**os.environ, 'FIXTURE_OFFLINE_KEY': 'fixture-api-key'}
            supervisor = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                rows = []
                for _ in range(300):
                    with sqlite3.connect(fixture.root / 'bank/accounts.sqlite3') as db:
                        try:
                            rows = db.execute('SELECT session_id,pid FROM native_execution').fetchall()
                        except sqlite3.OperationalError:
                            rows = []
                    if len(rows) == len(config) and all(row[0] == 'fixture-session-123' and row[1] for row in rows):
                        break
                    time.sleep(.1)
                self.assertEqual(len(rows), len(config))
                self.assertTrue(all(row[0] == 'fixture-session-123' and row[1] for row in rows))
                os.kill(supervisor.pid, signal.SIGKILL)
                supervisor.wait(timeout=10)
                result = subprocess.run([*args, '--resume-interrupted'], env=env, check=True,
                                        capture_output=True, text=True, timeout=90)
                output = json.loads(result.stdout)
                self.assertEqual([item['status'] for item in output], ['done'] * len(config))
                with sqlite3.connect(fixture.root / 'bank/accounts.sqlite3') as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_runs WHERE first_status='done' AND recovery_status='done'").fetchone()[0], len(config))
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0], len(config) * len(fixture.fixtures))
            finally:
                if supervisor.poll() is None:
                    supervisor.terminate()
                    supervisor.wait(timeout=10)
