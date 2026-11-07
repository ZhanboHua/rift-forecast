import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from lolarena.historical_controller import HistoricalController, digest, encode
from lolarena.history_accounts import HISTORICAL_ACCOUNTS, provision
from lolarena.validate import score_options


class FakeClock:
    def __init__(self):
        self.value = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


class HistoricalControllerTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(__file__).resolve().parents[1] / 'tmp'
        tmp.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=tmp)
        self.root = Path(self.temporary.name) / 'history'
        provision(self.root)
        self.clock = FakeClock()
        self.controller = HistoricalController(self.root, self.clock)
        self.tokens = {name: (self.root / 'credentials' / (name + '.token')).read_text().strip() for name in HISTORICAL_ACCOUNTS}
        self.material = Path(self.temporary.name) / 'material'
        self.material.mkdir()
        (self.material / 'pack.md').write_text('Fictional English source.')
        (self.root / 'result-evidence').mkdir()
        (self.root / 'result-evidence/raw.txt').write_text('Fictional result evidence.')
        self.anchor = datetime(2027, 1, 1, 12, tzinfo=timezone.utc)
        self.cutoff = self.anchor - timedelta(minutes=400)
        self.fixtures = [self.fixture('fictional-1', 1, 0), self.fixture('fictional-2', 3, 1)]
        (self.material / 'source.html').write_text('Fictional archived source.')
        captured = self.cutoff - timedelta(minutes=3)
        source = {'id': 'source-1', 'raw_path': 'source.html',
                  'raw_sha256': hashlib.sha256((self.material / 'source.html').read_bytes()).hexdigest(),
                  'capture_utc': captured.isoformat(),
                  'archive_url': 'https://web.archive.org/web/' + captured.strftime('%Y%m%d%H%M%S') + '/https://example.invalid/',
                  'memento_datetime': format_datetime(captured, usegmt=True)}
        self.attestation = {'state': 'VERIFIED_AS_OF', 'english_review': 'PASS', 'provenance_review': 'PASS',
                            'cutoff': self.cutoff.isoformat(), 'files': [{'path': 'pack.md',
                            'visible_at': (self.cutoff - timedelta(minutes=1)).isoformat(),
                            'sha256': hashlib.sha256((self.material / 'pack.md').read_bytes()).hexdigest(),
                            'source_ids': ['source-1']}], 'sources': [source],
                            'fixture_proofs': [{'match_id': x['match_id'], 'fixture_sha256': digest(encode(x)),
                            'source_id': 'source-1', 'known_at': (self.cutoff - timedelta(minutes=1)).isoformat(),
                            'review': 'PASS'} for x in self.fixtures]}

    def settle(self, day_id, match_id, score, result_id, source, published_at, evidence):
        review = self.controller.prepare_settlement(day_id, match_id, score, result_id, source, published_at, evidence)
        return self.controller.confirm_settlement(day_id, match_id, review['sha256'])

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, name, best_of, offset):
        return {'match_id': name, 'team_a': 'AAA', 'team_b': 'BBB', 'best_of': best_of,
                'start': (self.anchor + timedelta(hours=offset)).isoformat(),
                'announced_at': (self.cutoff - timedelta(minutes=2)).isoformat()}

    def prepare(self, fixtures=None, attestation=None):
        self.controller.prepare_day('day-1', self.anchor.isoformat(), (self.cutoff - timedelta(minutes=1)).isoformat(),
                                    self.fixtures if fixtures is None else fixtures, self.material,
                                    self.attestation if attestation is None else attestation)

    def prediction(self, index, best_of):
        return {'match_id': 'fictional-' + str(index), 'p_team_a_win': 0.6,
                'winner_bet': {'team': 'AAA', 'stake': 10},
                'score_bets': {key: 0 for key in score_options(best_of)},
                'rationale': 'Fictional analysis', 'sources': [], 'take_loan': False}

    def evidence(self, observed, match_id='fictional-1', score='1-0', result_id='result-1'):
        path = self.root / 'result-evidence' / (result_id + '.json')
        raw = self.root / 'result-evidence/raw.txt'
        path.write_text(json.dumps({'match_id': match_id, 'score': score, 'result_id': result_id,
                                    'source': 'fixture', 'published_at': observed.isoformat(),
                                    'observed_at': observed.isoformat(), 'status': 'VERIFIED',
                                    'raw_path': 'raw.txt', 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}))
        return {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'observed_at': observed.isoformat()}

    def test_requires_attested_cutoff_and_rejects_result_fields(self):
        with self.assertRaises(ValueError):
            self.prepare(attestation={**self.attestation, 'state': 'NOT_AGENT_READY'})
        with self.assertRaises(ValueError):
            self.prepare(fixtures=[{**self.fixtures[0], 'score': '1-0'}])
        with self.assertRaises(ValueError):
            self.prepare(fixtures=[{**self.fixtures[0], 'announced_at': (self.cutoff + timedelta(seconds=1)).isoformat()}])
        tampered = {**self.attestation, 'sources': [{**self.attestation['sources'][0], 'memento_datetime': 'Fri, 01 Jan 2021 00:00:00 GMT'}]}
        with self.assertRaises(ValueError):
            self.prepare(attestation=tampered)
        self.prepare()

    def test_sessions_atomic_lock_asof_settlement_and_account_continuity(self):
        self.prepare()
        with self.assertRaises(RuntimeError):
            self.controller.advance_clock('day-1', self.anchor.isoformat())
        with self.assertRaises(PermissionError):
            self.controller.submit('day-1', 'not-a-token', self.prediction(1, 1))
        for name in HISTORICAL_ACCOUNTS:
            self.controller.open_first('day-1', name, 60)
            self.controller.submit('day-1', self.tokens[name], self.prediction(1, 1))
            if name != HISTORICAL_ACCOUNTS[2]:
                self.controller.submit('day-1', self.tokens[name], self.prediction(2, 3))
            self.controller.finish_run('day-1', name, 'first', 'session-' + name, 'done')
        with self.assertRaises(RuntimeError):
            self.controller.seal_day('day-1')
        last = HISTORICAL_ACCOUNTS[2]
        with self.assertRaises(RuntimeError):
            self.controller.open_recovery('day-1', last, 'different-session')
        self.controller.open_recovery('day-1', last, 'session-' + last, 30)
        self.controller.submit('day-1', self.tokens[last], self.prediction(2, 3))
        self.controller.finish_run('day-1', last, 'recovery', 'session-' + last, 'done')
        self.assertEqual(self.controller.seal_day('day-1')['state'], 'locked')
        with self.assertRaises(RuntimeError):
            self.controller.submit('day-1', self.tokens[last], self.prediction(2, 3))
        finish1 = self.anchor + timedelta(hours=2)
        with self.assertRaises(ValueError):
            self.settle('day-1', 'fictional-1', '1-0', 'result-1', 'fixture', finish1.isoformat(), self.evidence(finish1))
        self.controller.advance_clock('day-1', finish1.isoformat())
        self.assertFalse(self.settle('day-1', 'fictional-1', '1-0', 'result-1', 'fixture', finish1.isoformat(), self.evidence(finish1))['duplicate'])
        self.assertTrue(self.settle('day-1', 'fictional-1', '1-0', 'result-1', 'fixture', finish1.isoformat(), self.evidence(finish1))['duplicate'])
        finish2 = self.anchor + timedelta(hours=3)
        with self.assertRaises(ValueError):
            self.settle('day-1', 'fictional-2', '2-0', 'result-2', 'fixture', finish2.isoformat(), self.evidence(finish2, 'fictional-2', '2-0', 'result-2'))
        self.controller.advance_clock('day-1', finish2.isoformat())
        self.assertEqual(self.settle('day-1', 'fictional-2', '2-0', 'result-2', 'fixture', finish2.isoformat(), self.evidence(finish2, 'fictional-2', '2-0', 'result-2'))['state'], 'settled')
        with closing(sqlite3.connect(self.root / 'bank/accounts.sqlite3')) as db:
            balances = dict(db.execute('SELECT account_id,balance FROM accounts'))
        self.assertAlmostEqual(sum(balances.values()), 3000.0)
        with self.assertRaises(RuntimeError):
            self.controller.prepare_day('rewind', self.anchor.isoformat(), (self.cutoff - timedelta(minutes=1)).isoformat(),
                                        self.fixtures, self.material, self.attestation)
        next_anchor = self.anchor + timedelta(days=1)
        next_cutoff = next_anchor - timedelta(minutes=400)
        fixture = {**self.fixtures[0], 'match_id': 'next-fictional-1', 'start': next_anchor.isoformat()}
        next_attestation = {**self.attestation, 'cutoff': next_cutoff.isoformat(),
                            'fixture_proofs': [{'match_id': fixture['match_id'], 'fixture_sha256': digest(encode(fixture)),
                                                'source_id': 'source-1', 'known_at': next_cutoff.isoformat(), 'review': 'PASS'}]}
        self.controller.prepare_day('day-2', next_anchor.isoformat(), next_cutoff.isoformat(), [fixture], self.material, next_attestation)
        self.assertEqual(self.controller.workspace_dates('day-1'), ('20270101', set()))
        self.assertEqual(self.controller.workspace_dates('day-2'), ('20270102', {'20270101'}))
        for account, expected in balances.items():
            view = self.controller.account_view('day-2', account)
            self.assertEqual(view['balance'], expected)
            self.assertEqual(len(view['history']), 2)
            self.assertEqual(len(view['standings']), len(self.tokens))
            self.assertTrue(all(set(row) == {'rank', 'account_id', 'net', 'joined_at', 'initial_chips', 'settled_count'} for row in view['standings']))
        with self.assertRaises(RuntimeError):
            self.controller.account_view('day-1', next(iter(self.tokens)))

    def test_loans_repeat_across_days_and_count_once_per_day(self):
        self.prepare()
        borrower, opponent, _ = HISTORICAL_ACCOUNTS
        with self.controller.connect() as db:
            db.execute('UPDATE accounts SET balance=40 WHERE account_id=?', (borrower,))

        def loan_bets(first_stake, second_stake):
            return [{**self.prediction(index, best_of), 'take_loan': True,
                     'winner_bet': {'team': 'BBB', 'stake': stake}}
                    for index, best_of, stake in ((1, 1, first_stake), (2, 3, second_stake))]

        def no_stake(index, best_of):
            return {**self.prediction(index, best_of), 'winner_bet': {'team': 'AAA', 'stake': 0}}

        for name in HISTORICAL_ACCOUNTS:
            self.controller.open_first('day-1', name, 60)
            if name == borrower:
                with self.assertRaises(ValueError):
                    self.controller.submit_many('day-1', self.tokens[name], loan_bets(540, 1))
                self.controller.submit_many('day-1', self.tokens[name], loan_bets(260, 250))
            elif name == opponent:
                self.controller.submit_many('day-1', self.tokens[name], [self.prediction(1, 1), self.prediction(2, 3)])
            else:
                self.controller.submit_many('day-1', self.tokens[name], [no_stake(1, 1), no_stake(2, 3)])
            self.controller.finish_run('day-1', name, 'first', name, 'done')
        self.controller.seal_day('day-1')
        first_loan = self.controller.account_view('day-1', borrower)
        self.assertEqual((first_loan['balance'], first_loan['debt'], first_loan['loans_taken']), (540, 600, 1))
        for index, score in ((1, '1-0'), (2, '2-0')):
            observed = self.anchor + timedelta(hours=index + 1)
            mid, rid = f'fictional-{index}', f'result-{index}'
            self.controller.advance_clock('day-1', observed.isoformat())
            self.settle('day-1', mid, score, rid, 'fixture', observed.isoformat(),
                                   self.evidence(observed, mid, score, rid))

        next_anchor = self.anchor + timedelta(days=1)
        cutoff = next_anchor - timedelta(minutes=400)
        fixture = {**self.fixtures[0], 'start': next_anchor.isoformat()}
        attestation = {**self.attestation, 'cutoff': cutoff.isoformat(),
                       'fixture_proofs': [{'match_id': fixture['match_id'], 'fixture_sha256': digest(encode(fixture)),
                                           'source_id': 'source-1', 'known_at': cutoff.isoformat(), 'review': 'PASS'}]}
        self.controller.prepare_day('day-2', next_anchor.isoformat(), cutoff.isoformat(), [fixture], self.material, attestation)
        view = self.controller.account_view('day-2', borrower)
        self.assertEqual(view['balance'], 30)
        self.assertTrue(view['loan_eligible'])
        self.assertEqual(view['cap_with_loan'], 530)
        for name in HISTORICAL_ACCOUNTS:
            self.controller.open_first('day-2', name, 60)
            if name == borrower:
                self.controller.submit('day-2', self.tokens[name], loan_bets(530, 0)[0])
            else:
                self.controller.submit('day-2', self.tokens[name], no_stake(1, 1))
            self.controller.finish_run('day-2', name, 'first', name, 'done')
        self.controller.seal_day('day-2')
        second_loan = self.controller.account_view('day-2', borrower)
        self.assertEqual((second_loan['balance'], second_loan['debt'], second_loan['loans_taken']), (530, 1200, 2))

    def test_two_summary_recoveries_preserve_existing_receipts(self):
        self.prepare()
        account = HISTORICAL_ACCOUNTS[0]
        session = 'same-session'
        self.controller.open_first('day-1', account, 3600)
        self.controller.submit_many('day-1', self.tokens[account], [self.prediction(1, 1), self.prediction(2, 3)])
        before = self.controller.receipt_view('day-1', account)
        self.controller.finish_run('day-1', account, 'first', session, 'done')
        with self.assertRaises(RuntimeError):
            self.controller.open_recovery('day-1', account, session)
        for status in ('U', 'done'):
            self.controller.open_recovery('day-1', account, session, missing_summary=True)
            self.controller.finish_run('day-1', account, 'recovery', session, status)
        with self.assertRaises(RuntimeError):
            self.controller.open_recovery('day-1', account, session, missing_summary=True)
        self.assertEqual(self.controller.receipt_view('day-1', account), before)
        with self.controller.connect() as db:
            self.assertEqual(db.execute('SELECT attempt,missing_predictions,missing_summary,status '
                                        'FROM historical_recovery_attempts ORDER BY attempt').fetchall(),
                             [(1, 0, 1, 'U'), (2, 0, 1, 'done')])

    def test_batch_validation_is_atomic_and_offline_edits_are_rejected(self):
        self.prepare()
        name = HISTORICAL_ACCOUNTS[0]
        with self.assertRaises(RuntimeError):
            self.controller.match_view('day-1', name)
        self.controller.open_first('day-1', name, 60)
        self.assertEqual(len(self.controller.match_view('day-1', name)['matches']), 2)
        good = self.prediction(1, 1)
        invalid = {**self.prediction(2, 3), 'p_team_a_win': 1.5}
        with self.assertRaises(ValueError):
            self.controller.submit_many('day-1', self.tokens[name], [good, invalid])
        with closing(sqlite3.connect(self.root / 'bank/accounts.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0], 0)
        self.controller.submit_many('day-1', self.tokens[name], [good, self.prediction(2, 3)])
        self.assertTrue(self.controller.receipt_view('day-1', name)['matches']['fictional-1']['submitted'])
        self.controller.open_first('day-1', HISTORICAL_ACCOUNTS[1], 60)
        self.assertFalse(self.controller.receipt_view('day-1', HISTORICAL_ACCOUNTS[1])['matches']['fictional-1']['submitted'])
        self.assertTrue(self.controller.submit('day-1', self.tokens[name], good)['duplicate'])
        with self.assertRaises(RuntimeError):
            self.controller.submit('day-1', self.tokens[name], {**good, 'p_team_a_win': 0.7})

    def test_missed_deadline_is_abstention_with_infrastructure_failure_separate(self):
        self.prepare()
        for name in HISTORICAL_ACCOUNTS:
            self.controller.open_first('day-1', name, 1)
        self.clock.value += timedelta(seconds=2)
        with self.assertRaises(RuntimeError):
            self.controller.submit('day-1', self.tokens[HISTORICAL_ACCOUNTS[0]], self.prediction(1, 1))
        for name in HISTORICAL_ACCOUNTS:
            self.controller.finish_run('day-1', name, 'first', None, 'U')
        self.controller.seal_day('day-1')
        with closing(sqlite3.connect(self.root / 'bank/accounts.sqlite3')) as db:
            lock = json.loads(db.execute('SELECT payload FROM historical_locks').fetchone()[0])
        self.assertTrue(all(item['status'] == 'abstain' and item['execution'] == 'U'
                            for rows in lock['entries'].values() for item in rows.values()))
        self.controller.advance_clock('day-1', (self.anchor + timedelta(hours=2)).isoformat())
        with self.assertRaises(RuntimeError):
            self.settle('day-1', 'fictional-1', '1-0', 'id', 'fixture', (self.anchor + timedelta(hours=2)).isoformat(), self.evidence(self.anchor + timedelta(hours=2), result_id='id'))


if __name__ == '__main__':
    unittest.main()
