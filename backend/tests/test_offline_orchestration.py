"""Exercise offline Bank orchestration in isolated fictional containers."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lolarena.orchestrator import run_day
from test_historical_controller import HistoricalControllerTest


@unittest.skipUnless(os.environ.get('RIFT_OFFLINE_TEST_AGENT_IMAGE'), 'requires a fictional native CLI image')
class OfflineOrchestrationTest(unittest.TestCase):
    def test_offline_bank_receipts_and_native_recovery(self):
        fixture = HistoricalControllerTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.prepare()
        control_parent = Path(os.environ.get('RIFT_OFFLINE_CONTROL_PARENT', str(fixture.root.parent)))
        with tempfile.TemporaryDirectory(prefix='rift-gw-', dir=control_parent) as control:
            config = {account: {'protocol': 'offline_codex', 'command': ['codex'], 'model': 'fixture-model',
                                'timeout_seconds': 30, 'max_recoveries': 1,
                                'image': os.environ['RIFT_OFFLINE_TEST_AGENT_IMAGE'],
                                'gateway_url': 'https://gateway.example/v1', 'control_dir': control,
                                'env_map': {'CODEX_API_KEY': 'FIXTURE_OFFLINE_KEY'}}
                      for account in fixture.controller.accounts}
            with patch.dict(os.environ, {'FIXTURE_OFFLINE_KEY': 'fixture-api-key'}):
                results = run_day(fixture.controller, 'day-1', config, fixture.root / 'offline-work',
                                  fixture.material, fixture.attestation)
        self.assertEqual([r['status'] for r in results], ['done'] * len(results))
        self.assertTrue(all(r['session_id'] == 'fixture-session-123' and r['recoveries'] == 1 for r in results))
        with fixture.controller.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0],
                             len(fixture.controller.accounts) * len(fixture.fixtures))
