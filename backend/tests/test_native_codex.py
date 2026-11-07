"""Test native session recovery using a fictional JSONL CLI."""
import sys
import unittest
from pathlib import Path
from test_orchestrator import OrchestratorTest
from lolarena.orchestrator import run_day


class NativeSessionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = OrchestratorTest(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binary = str(Path(__file__).parent / 'fixtures/mock_codex_cli.py')

    def spec(self, mismatch=False):
        return {'protocol': 'codex', 'command': [sys.executable, self.binary], 'model': 'fixture-model',
                'timeout_seconds': 30, 'max_recoveries': 1,
                'env_values': {'MISMATCH': '1'} if mismatch else {}}

    def run_fixture(self, a, b):
        f = self.fixture
        return run_day(f.controller, 'fixture-day', {'model-a': a, 'model-b': b},
                       f.root / 'native-work', f.material, f.attestation)

    def test_session_resume_uses_exact_emitted_id(self):
        result = self.run_fixture(self.spec(), self.spec())
        self.assertEqual([r['status'] for r in result], ['done', 'done'])
        self.assertTrue(all(r['session_id'] == 'fixture-session-123' and r['recoveries'] == 1 for r in result))
        with self.fixture.controller.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_runs WHERE first_status='done' AND recovery_status='done'").fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0], 2)

    def test_timeout_resumes_only_observed_session(self):
        a = self.spec()
        a['timeout_seconds'] = 1
        a['env_values'] = {'TIMEOUT_FIRST': '1'}
        result = self.run_fixture(a, self.spec())
        self.assertEqual([r['status'] for r in result], ['done', 'done'])
        self.assertEqual(result[0]['session_id'], 'fixture-session-123')
        self.assertEqual(result[0]['recoveries'], 1)

    def test_missing_session_never_retries(self):
        a = self.spec()
        a['env_values'] = {'NO_SESSION': '1'}
        result = self.run_fixture(a, self.spec())
        self.assertEqual([r['status'] for r in result], ['U', 'done'])
        with self.fixture.controller.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_recovery_attempts WHERE account_id='model-a'").fetchone()[0], 0)

    def test_recovery_timeout_rejected_before_any_launch(self):
        bad = self.spec()
        bad['timeout_seconds'] = 1801
        with self.assertRaises(ValueError):
            self.run_fixture(bad, self.spec())
        with self.fixture.controller.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_runs WHERE first_status!='pending'").fetchone()[0], 0)

    def test_changed_session_stays_unknown(self):
        result = self.run_fixture(self.spec(True), self.spec())
        self.assertEqual([r['status'] for r in result], ['U', 'done'])
        with self.fixture.controller.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_submissions WHERE account_id='model-a'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT first_status,recovery_status FROM historical_runs WHERE account_id='model-a'").fetchone(), ('U', 'U'))
