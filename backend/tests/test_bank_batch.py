"""Check fictional daily submissions, identity isolation and stake reservation."""
import hashlib
import json
import concurrent.futures
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient
from lolarena.bank import BankError, create_app
from lolarena.bank_batch import BatchBank
from lolarena.validate import score_options


def fixture(track='offline'):
    first = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=2, minute=0, second=0, microsecond=0)
    known = first - timedelta(hours=8)
    matches = [{'match_id': 'SYNTHETIC-M' + str(i), 'team_a': 'A' + str(i), 'team_b': 'B' + str(i), 'best_of': bo,
                'start': (first + timedelta(hours=i-1)).isoformat(), 'announced_at': known.isoformat()}
               for i, bo in enumerate((1, 3, 5), 1)]
    snapshot = {'balance': 1000, 'available': 1000, 'cap': 1000, 'cap_with_loan': 1000, 'debt': 0, 'loan_eligible': False, 'loans_taken': 0}
    h = lambda x: hashlib.sha256(x.encode()).hexdigest()
    return {'mode': 'synthetic', 'allowed_hosts': ['testserver'],
            'accounts': {'alpha': {'token_sha256': h('alpha-token'), 'active': True}, 'beta': {'token_sha256': h('beta-token'), 'active': True}},
            'round': {'round_id': 'fixture-day', 'scheduled_batch_synthetic': True, 'track': track,
                      'day_anchor': first.isoformat(), 'anchor_announced_at': known.isoformat(),
                      'deadline': (first - timedelta(minutes=10)).isoformat(), 'matches': matches,
                      'accounts': {'alpha': snapshot, 'beta': snapshot}}}


def bet(match, stake=100):
    return {'match_id': match['match_id'], 'p_team_a_win': .6, 'winner_bet': {'team': match['team_a'], 'stake': stake},
            'score_bets': {score: 0 for score in score_options(match['best_of'])},
            'rationale': 'fictional test', 'sources': [], 'take_loan': False}


class BatchTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.db = Path(self.tmp.name) / 'bank.sqlite3'
        self.cfg = fixture('online')
        self.bank = BatchBank(self.cfg, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def phase(self, phase):
        with closing(self.bank.connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO round_control VALUES (?,?,?)',
                       ('fixture-day', phase, (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()))
            db.execute('INSERT OR IGNORE INTO batch_visibility VALUES (?,1)', ('fixture-day',))

    def test_all_matches_one_atomic_submission_and_revision(self):
        matches = self.cfg['round']['matches']
        self.phase('first')
        result = self.bank.submit_many('alpha', [bet(m, 200) for m in matches])
        self.assertEqual(len(result['matches']), 3)
        self.assertEqual(self.bank.account('alpha')['reserved'], 600)
        self.assertTrue(all(not v['submitted'] for v in self.bank.receipt('beta')['matches'].values()))
        self.assertTrue(self.bank.submit_many('alpha', [bet(m, 200) for m in matches])['duplicate'])
        with self.assertRaises(BankError):
            self.bank.submit('alpha', bet(matches[0], 500))
        self.phase('review')
        updated = [bet(matches[0], 700), bet(matches[1], 100), bet(matches[2], 200)]
        self.bank.submit_many('alpha', updated)
        self.assertEqual(self.bank.account('alpha')['reserved'], 1000)
        with self.assertRaises(BankError):
            self.bank.submit('alpha', bet(matches[0], 701))
        self.assertEqual(self.bank.account('alpha')['reserved'], 1000)
        with closing(self.bank.connect()) as db, db:
            rows = db.execute('SELECT match_id,version,phase FROM batch_versions ORDER BY match_id,version').fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual([row[2] for row in rows].count('review'), 2)

    def test_formats_unknown_match_and_future_announcement_rejected(self):
        matches = self.cfg['round']['matches']
        self.phase('first')
        invalid = bet(matches[0])
        invalid['score_bets'] = {'3-0': 1}
        with self.assertRaises(BankError):
            self.bank.submit_many('alpha', [bet(matches[1]), invalid])
        self.assertTrue(all(not x['submitted'] for x in self.bank.receipt('alpha')['matches'].values()))
        invalid = bet(matches[0])
        invalid['match_id'] = 'REAL-M1'
        with self.assertRaises(BankError):
            self.bank.submit('alpha', invalid)
        cfg = fixture()
        cfg['round']['matches'][1]['announced_at'] = cfg['round']['matches'][1]['start']
        with self.assertRaises(ValueError):
            BatchBank(cfg, Path(self.tmp.name) / 'invalid.sqlite3')

    def test_track_visibility_and_independent_anchor(self):
        offline, online = fixture('offline'), fixture('online')
        anchor = datetime.fromisoformat(offline['round']['day_anchor'])
        for cfg in (offline, online):
            cfg['round']['matches'] = cfg['round']['matches'][1:]
            cfg['round']['matches'][0]['announced_at'] = (anchor - timedelta(minutes=330)).isoformat()
        with self.assertRaises(ValueError):
            BatchBank(offline, Path(self.tmp.name) / 'hidden-offline.sqlite3')
        bank = BatchBank(online, Path(self.tmp.name) / 'visible-online.sqlite3')
        self.assertEqual(bank.anchor, anchor)
        self.assertEqual(bank.match('alpha')['anchor'], anchor.isoformat())
        self.assertEqual(bank.match('alpha')['matches'], [])
        with closing(bank.connect()) as db, db:
            db.execute('INSERT INTO batch_visibility VALUES (?,1)', ('fixture-day',))
        self.assertEqual(len(bank.match('alpha')['matches']), 2)
        online['round']['matches'][0]['announced_at'] = (anchor - timedelta(minutes=299)).isoformat()
        with self.assertRaises(ValueError):
            BatchBank(online, Path(self.tmp.name) / 'hidden-online.sqlite3')

    def test_anchor_requires_pre_cutoff_evidence_and_correct_day(self):
        cfg = fixture()
        anchor = datetime.fromisoformat(cfg['round']['day_anchor'])
        cfg['round']['anchor_announced_at'] = (anchor - timedelta(minutes=359)).isoformat()
        with self.assertRaises(ValueError):
            BatchBank(cfg, Path(self.tmp.name) / 'late-anchor.sqlite3')
        cfg = fixture()
        del cfg['round']['day_anchor']
        with self.assertRaises(ValueError):
            BatchBank(cfg, Path(self.tmp.name) / 'no-anchor.sqlite3')

    def test_visibility_cutoff_includes_the_exact_instant(self):
        for track, offset in (('offline', 360), ('online', 300)):
            cfg = fixture(track)
            anchor = datetime.fromisoformat(cfg['round']['day_anchor'])
            cfg['round']['matches'][0]['announced_at'] = (anchor - timedelta(minutes=offset)).isoformat()
            BatchBank(cfg, Path(self.tmp.name) / (track + '-exact.sqlite3'))
            cfg['round']['matches'][0]['announced_at'] = (anchor - timedelta(minutes=offset) + timedelta(microseconds=1)).isoformat()
            with self.assertRaises(ValueError):
                BatchBank(cfg, Path(self.tmp.name) / (track + '-late.sqlite3'))

    def test_loan_is_used_once_for_the_whole_day(self):
        snapshot = self.cfg['round']['accounts']['alpha']
        snapshot.update(balance=40, available=40, cap=40, cap_with_loan=540, loan_eligible=True)
        self.phase('first')
        first, second, third = [bet(match, stake) for match, stake in zip(self.cfg['round']['matches'], (200, 200, 140))]
        first['take_loan'] = True
        self.bank.submit_many('alpha', [first, second, third])
        self.assertEqual(self.bank.account('alpha')['remaining_cap'], 0)
        self.phase('review')
        first['take_loan'] = False
        with self.assertRaises(BankError):
            self.bank.submit('alpha', first)
        self.assertTrue(self.bank.account('alpha')['loan_requested'])

    def test_concurrent_submissions_cannot_overdraw_daily_cap(self):
        self.phase('first')
        matches = self.cfg['round']['matches']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(self.bank.submit, 'alpha', bet(match, 600)) for match in matches[:2]]
        outcomes = []
        for future in pending:
            try:
                future.result()
                outcomes.append('accepted')
            except BankError as exc:
                self.assertEqual(exc.status, 409)
                outcomes.append('rejected')
        self.assertEqual(sorted(outcomes), ['accepted', 'rejected'])
        self.assertEqual(self.bank.account('alpha')['reserved'], 600)

    def test_expired_missing_waits_for_authoritative_finalization(self):
        self.phase('first')
        self.bank.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        states = self.bank.receipt('alpha')['matches'].values()
        self.assertTrue(all(item['state'] == 'awaiting_finalization' for item in states))

    def test_mcp_and_gui_share_same_private_batch(self):
        with TestClient(create_app(self.cfg, self.db)) as client:
            headers = {'Authorization': 'Bearer alpha-token'}
            self.assertEqual(client.get('/api/match', headers=headers).json()['matches'], [])
            self.assertEqual(client.get('/api/submission', headers=headers).json()['matches'], {})
            self.assertEqual(client.post('/api/submission', headers=headers,
                                         json=[bet(self.cfg['round']['matches'][0])]).status_code, 409)
            self.phase('first')
            info = client.get('/api/match', headers=headers).json()
            self.assertEqual(len(info['matches']), 3)
            self.assertEqual(client.post('/api/submission', headers=headers,
                                         json=[bet(m) for m in self.cfg['round']['matches']]).status_code, 200)
            self.assertFalse(client.get('/api/submission', headers={'Authorization':'Bearer beta-token'}).json()['matches']['SYNTHETIC-M1']['submitted'])
            mcp_headers = {**headers, 'Accept':'application/json, text/event-stream', 'Content-Type':'application/json'}
            call = {'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{'name':'bank_get_match', 'arguments':{}}}
            output = client.post('/mcp', headers=mcp_headers, json=call)
            self.assertEqual(len(json.loads(output.json()['result']['content'][0]['text'])['matches']), 3)


if __name__ == '__main__':
    unittest.main()
