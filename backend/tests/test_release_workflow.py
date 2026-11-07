"""Check review, roster, and migration against fictional ledgers."""
import concurrent.futures
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from test_historical_controller import HistoricalControllerTest
from lolarena.migrate_ledger import migrate
from lolarena.source_import import import_result
from lolarena.historical_control_api import create_app
from starlette.testclient import TestClient

class ReleaseWorkflowTest(HistoricalControllerTest):
    def locked_day(self):
        self.prepare()
        for account in self.controller.accounts:
            self.controller.open_first('day-1', account, 60)
            self.controller.finish_run('day-1', account, 'first', 'session', 'done')
            self.controller.open_recovery('day-1', account, 'session', 60)
            self.controller.finish_run('day-1', account, 'recovery', 'session', 'done')
        self.controller.seal_day('day-1')

    def test_review_requires_confirmation_and_rechecks_evidence(self):
        self.locked_day()
        when = self.anchor + timedelta(hours=2)
        self.controller.advance_clock('day-1', when.isoformat())
        evidence = self.evidence(when)
        review = self.controller.prepare_settlement('day-1', 'fictional-1', '1-0', 'result-1', 'fixture', when.isoformat(), evidence)
        with self.controller.connect() as db:
            before = db.execute('SELECT account_id,balance FROM accounts ORDER BY account_id').fetchall()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_results').fetchone()[0], 0)
        proof = self.root / 'result-evidence' / evidence['path']
        original = proof.read_bytes()
        proof.write_bytes(original + b' ')
        with self.assertRaises(ValueError):
            self.controller.confirm_settlement('day-1', 'fictional-1', review['sha256'])
        with self.controller.connect() as db:
            self.assertEqual(before, db.execute('SELECT account_id,balance FROM accounts ORDER BY account_id').fetchall())
        proof.write_bytes(original)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.controller.confirm_settlement('day-1', 'fictional-1', review['sha256']), range(2)))
        self.assertEqual(sum(result['duplicate'] is False for result in results), 1)

    def test_conflicting_candidate_is_blocked(self):
        self.locked_day()
        when = self.anchor + timedelta(hours=2)
        evidence = self.evidence(when)
        review = self.controller.prepare_settlement('day-1', 'fictional-1', '1-0', 'result-1', 'fixture', when.isoformat(), evidence)
        conflict = self.controller.prepare_settlement('day-1', 'fictional-1', '0-1', 'result-1', 'fixture', when.isoformat(), evidence)
        self.assertEqual(conflict['status'], 'conflict')
        with self.assertRaises(RuntimeError):
            self.controller.confirm_settlement('day-1', 'fictional-1', review['sha256'])

    def test_roster_changes_and_snapshot(self):
        self.controller.change_roster('new-agent', 'add')
        self.controller.change_roster('new-agent', 'disable')
        self.controller.change_roster('new-agent', 'enable')
        with self.controller.connect() as db:
            self.assertEqual(db.execute("SELECT balance,debt FROM accounts WHERE account_id='new-agent'").fetchone(), (1000, 0))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM roster_audit WHERE account_id='new-agent'").fetchone()[0], 3)
        self.prepare()
        with self.assertRaises(RuntimeError):
            self.controller.change_roster('new-agent', 'disable')
        with self.controller.connect() as db:
            self.assertIn('new-agent', self.controller._day_accounts(db, 'day-1'))

    def test_roster_snapshot_survives_later_changes(self):
        self.prepare()
        with self.controller.connect() as db:
            original = self.controller._day_accounts(db, 'day-1')
            db.execute("UPDATE historical_days SET state='settled' WHERE day_id='day-1'")
        self.controller.change_roster(original[0], 'disable')
        self.controller.change_roster('later-agent', 'add')
        with self.controller.connect() as db:
            self.assertEqual(self.controller._day_accounts(db, 'day-1'), original)
            self.assertEqual(db.execute('SELECT active FROM roster WHERE account_id=?', (original[0],)).fetchone()[0], 0)

    def test_control_auth_host_and_origin(self):
        self.prepare()
        app = create_app(self.controller, 'admin-test-token', ['testserver'])
        path = '/v1/days/day-1/review-status'
        payload = {'match_id': 'fictional-1'}
        with TestClient(app) as client:
            self.assertEqual(client.post(path, json=payload).status_code, 401)
            participant = next(iter(self.tokens.values()))
            self.assertEqual(client.post(path, json=payload, headers={'Authorization': 'Bearer ' + participant}).status_code, 401)
            admin = {'Authorization': 'Bearer admin-test-token'}
            self.assertEqual(client.post(path, json=payload, headers={**admin, 'Host': 'other'}).status_code, 403)
            self.assertEqual(client.post(path, json=payload, headers={**admin, 'Origin': 'http://other'}).status_code, 403)
            self.assertEqual(client.post(path, json=payload, headers=admin).status_code, 400)

    def test_source_import_and_migration_copy(self):
        base = Path(self.temporary.name)
        raw = base / 'result.raw'
        raw.write_text('fictional result')
        metadata = base / 'result.json'
        metadata.write_text(json.dumps({'match_id': 'm', 'score': '1-0', 'result_id': 'r', 'source': 'fixture', 'published_at': '2030-01-01T00:00:00+00:00', 'observed_at': '2030-01-01T00:01:00+00:00'}))
        result = import_result(raw, metadata, base / 'evidence')
        self.assertEqual(hashlib.sha256((base / 'evidence' / result['path']).read_bytes()).hexdigest(), result['sha256'])
        legacy = base / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute('CREATE TABLE accounts(account_id TEXT PRIMARY KEY, token_sha256 TEXT, balance REAL, debt REAL, loans_taken INTEGER)')
            db.execute("INSERT INTO accounts VALUES ('a','digest',900,0,0)")
        original = legacy.read_bytes()
        dest = base / 'migrated.sqlite3'
        migrate(legacy, dest)
        self.assertEqual(legacy.read_bytes(), original)
        with closing(sqlite3.connect(dest)) as db:
            self.assertEqual(db.execute('SELECT account_id,joined_at,initial_chips FROM roster').fetchone(), ('a', 'unknown', 1000))
        with self.assertRaises(FileExistsError):
            migrate(legacy, dest)
