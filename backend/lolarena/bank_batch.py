"""Account-isolated multi-match synthetic Bank for one match day."""
import hashlib
import hmac
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .bank import Bank, BankError, CREDENTIAL, utc_time
from .market import standings
from .validate import validate


class BatchBank(Bank):
    _batch_class = True

    def __init__(self, config, database):
        round_data = config.get('round', {})
        matches = round_data.get('matches')
        if (config.get('mode') != 'synthetic' or round_data.get('scheduled_batch_synthetic') is not True
                or round_data.get('track') not in ('offline', 'online') or not isinstance(matches, list)
                or not 0 <= len(matches) <= 30):
            raise ValueError('Invalid synthetic daily round')
        try:
            anchor = utc_time(round_data['day_anchor'])
            anchor_announced = utc_time(round_data['anchor_announced_at'])
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError('Explicit dated match-day anchor is required') from exc
        if anchor_announced > anchor - timedelta(minutes=360):
            raise ValueError('Match-day anchor was not known at the first prediction time')
        ids, starts = set(), []
        for match in matches:
            if (not isinstance(match, dict) or set(match) != {'match_id', 'team_a', 'team_b', 'best_of', 'start', 'announced_at'}
                    or not isinstance(match['match_id'], str) or not re.fullmatch(r'SYNTHETIC-M[1-9][0-9]*', match['match_id'])
                    or match['match_id'] in ids or type(match['best_of']) is not int or match['best_of'] not in (1, 3, 5)
                    or not all(isinstance(match[k], str) and re.fullmatch(r'[A-Z][A-Z0-9]{0,9}', match[k]) for k in ('team_a', 'team_b'))
                    or match['team_a'] == match['team_b']):
                raise ValueError('Invalid fictional match')
            ids.add(match['match_id'])
            starts.append(utc_time(match['start']))
        local_zone = timezone(timedelta(hours=8))
        if any(start.astimezone(local_zone).date() != anchor.astimezone(local_zone).date() for start in starts):
            raise ValueError('Synthetic matches must share the Beijing match day')
        if any(start < anchor for start in starts):
            raise ValueError('A match cannot start before the match-day anchor')
        cutoff = anchor - timedelta(minutes=360 if round_data['track'] == 'offline' else 300)
        if any(utc_time(match['announced_at']) > cutoff for match in matches):
            raise ValueError('Match was not known at the first prediction time')
        if utc_time(round_data['deadline']) != anchor - timedelta(minutes=10):
            raise ValueError('Daily deadline must be ten minutes before the earliest start')
        self.matches = {match['match_id']: match for match in matches}
        self.anchor = anchor
        super().__init__(config, database)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS batch_submissions (round_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL, digest TEXT NOT NULL, prediction TEXT NOT NULL, accepted_at TEXT NOT NULL, PRIMARY KEY(round_id, account_id, match_id))')
            db.execute('CREATE TABLE IF NOT EXISTS batch_versions (round_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, prediction TEXT NOT NULL, accepted_at TEXT NOT NULL, phase TEXT NOT NULL, PRIMARY KEY(round_id, account_id, match_id, version))')
            db.execute('CREATE TABLE IF NOT EXISTS batch_account_projections (round_id TEXT NOT NULL, account_id TEXT NOT NULL, seq INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(round_id, account_id))')
            db.execute('CREATE TABLE IF NOT EXISTS batch_match_projections (round_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(round_id, account_id, match_id))')
            db.execute('CREATE TABLE IF NOT EXISTS batch_lock_projections (round_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(round_id, account_id, match_id))')
            db.execute('CREATE TABLE IF NOT EXISTS batch_visibility (round_id TEXT PRIMARY KEY, opened INTEGER NOT NULL CHECK(opened=1))')

    @staticmethod
    def _verified(row):
        if not hmac.compare_digest(hashlib.sha256(row[0].encode()).hexdigest(), row[1]):
            raise BankError('Batch data failed integrity check', 503)
        return json.loads(row[0])

    def account(self, identity):
        snapshot = self.round.get('accounts', {}).get(identity)
        if snapshot is None:
            raise BankError('Account is not enrolled in this round', 403)
        fields = ('balance', 'available', 'cap', 'cap_with_loan', 'debt', 'loan_eligible', 'loans_taken')
        data = {'account_id': identity, 'track': self.round['track'], 'round_id': self.round['round_id'],
                **{key: snapshot[key] for key in fields if key in snapshot}}
        with closing(self.connect()) as db:
            db.execute('BEGIN')
            projections = {name: self._verified((body, checksum)) for name, body, checksum in db.execute(
                'SELECT account_id,payload,digest FROM batch_account_projections WHERE round_id=?', (self.round['round_id'],))}
            history = db.execute('''SELECT r.match_id,r.payload,r.digest,s.prediction,s.digest
                FROM batch_match_projections AS r LEFT JOIN batch_submissions AS s
                ON (s.round_id,s.account_id,s.match_id)=(r.round_id,r.account_id,r.match_id)
                WHERE r.round_id=? AND r.account_id=? ORDER BY r.match_id''', (self.round['round_id'], identity)).fetchall()
            rows = db.execute('SELECT prediction,digest FROM batch_submissions WHERE round_id=? AND account_id=?',
                              (self.round['round_id'], identity)).fetchall()
        data['history'] = []
        for match_id, body, checksum, bet, bet_hash in history:
            result = self._verified((body, checksum))
            prediction = self._verified((bet, bet_hash)) if bet is not None else {}
            data['history'].append({'match_id': match_id, 'status': result['status'], 'pnl': result['pnl'],
                                    **{key: prediction[key] for key in ('p_team_a_win', 'winner_bet', 'score_bets') if key in prediction}})
        data['standings'] = standings({name: projections.get(name, {}).get('net', round(item['balance'] - item.get('debt', 0), 2))
                                      for name, item in self.round['accounts'].items()})
        if identity in projections:
            data.update(projections[identity])
            data['open_stakes'] = round(max(0, data['balance'] - data['available']), 2)
            return data
        bets = [self._verified(row) for row in rows]
        reserved = sum(Decimal(str(p['winner_bet']['stake'])) + sum(Decimal(str(x)) for x in p['score_bets'].values()) for p in bets)
        loan = any(p['take_loan'] for p in bets) and snapshot.get('loan_eligible', False)
        cap = Decimal(str(snapshot['cap_with_loan'] if loan else snapshot['cap']))
        data['reserved'] = float(reserved)
        data['open_stakes'] = round(max(0, snapshot['balance'] - snapshot['available']) + float(reserved), 2)
        data['remaining_cap'] = float(max(Decimal('0'), cap - reserved))
        data['loan_requested'] = bool(loan)
        data['available'] = float(max(Decimal('0'), Decimal(str(snapshot['available'])) - reserved))
        return data

    def match(self, identity):
        self.account(identity)
        fields = ('match_id', 'team_a', 'team_b', 'best_of')
        return {'round_id': self.round['round_id'], 'track': self.round['track'], 'anchor': self.anchor.isoformat(),
                'deadline': self.deadline.isoformat(), 'matches': [
                    {**{k: m[k] for k in fields}, 'start': utc_time(m['start']).isoformat()}
                    for m in self.matches.values()] if self._visible() else []}

    def _visible(self):
        with closing(self.connect()) as db:
            return bool(db.execute('SELECT 1 FROM batch_visibility WHERE round_id=? AND opened=1',
                                   (self.round['round_id'],)).fetchone())

    def receipt(self, identity):
        self.account(identity)
        if not self._visible():
            return {'round_id': self.round['round_id'], 'matches': {}}
        with closing(self.connect()) as db:
            rows = db.execute('SELECT match_id,digest,accepted_at FROM batch_submissions WHERE round_id=? AND account_id=?',
                              (self.round['round_id'], identity)).fetchall()
            projections = db.execute('SELECT match_id,payload,digest FROM batch_match_projections WHERE round_id=? AND account_id=?',
                                     (self.round['round_id'], identity)).fetchall()
            locks = db.execute('SELECT match_id,payload,digest FROM batch_lock_projections WHERE round_id=? AND account_id=?',
                               (self.round['round_id'], identity)).fetchall()
        received = {r[0]: {'submitted': True, 'sha256': r[1], 'accepted_at': r[2], 'state': 'accepted_not_settled'} for r in rows}
        for match_id, payload, digest in locks:
            data = self._verified((payload, digest))
            if data['status'] == 'ok':
                if match_id not in received:
                    raise BankError('Locked batch submission is missing', 503)
                if not hmac.compare_digest(received[match_id]['sha256'], data.get('sha256') or ''):
                    raise BankError('Locked batch submission changed', 503)
                received[match_id]['state'] = 'locked_not_settled'
            else:
                received[match_id] = {'submitted': match_id in received,
                                      'state': 'abstained' if data['status'] == 'abstain' else 'U'}
        for match_id, payload, digest in projections:
            data = self._verified((payload, digest))
            received[match_id] = {'submitted': data['status'] == 'ok', 'state': 'settled', 'result': data}
        missing_state = 'awaiting_finalization' if datetime.now(timezone.utc) >= self.deadline else 'open'
        return {'round_id': self.round['round_id'], 'matches': {match_id: received.get(match_id, {'submitted': False, 'state': missing_state})
                                                               for match_id in self.matches}}

    def submit(self, identity, prediction):
        return self.submit_many(identity, [prediction])

    def submit_many(self, identity, predictions):
        self.account(identity)
        if not self._visible():
            raise BankError('Daily fixtures are not open yet', 409)
        if not isinstance(predictions, list) or not 1 <= len(predictions) <= len(self.matches):
            raise BankError('Expected one or more daily predictions')
        candidates = {}
        for item in predictions:
            if not isinstance(item, dict) or not isinstance(item.get('match_id'), str) or item['match_id'] not in self.matches:
                raise BankError('Unknown match in daily batch')
            mid = item['match_id']
            if mid in candidates:
                raise BankError('Duplicate match in daily batch')
            match = self.matches[mid]
            clean, errors = validate(item, mid, match['team_a'], match['team_b'], match['best_of'])
            if errors:
                raise BankError('Invalid prediction: ' + '; '.join(errors))
            amounts = [clean['winner_bet']['stake'], *clean['score_bets'].values()]
            if any(Decimal(str(amount)).normalize().as_tuple().exponent < -2 for amount in amounts):
                raise BankError('Stake amounts must use at most two decimal places')
            body = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            credential = CREDENTIAL.get()
            if credential and credential in body:
                raise BankError('Prediction contains an account credential')
            candidates[mid] = (hashlib.sha256(body.encode()).hexdigest(), body)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            round_id = self.round['round_id']
            rows = db.execute('SELECT match_id,digest,prediction,accepted_at FROM batch_submissions WHERE round_id=? AND account_id=?',
                              (round_id, identity)).fetchall()
            existing = {row[0]: row[1:] for row in rows}
            for _, (digest, body, _) in existing.items():
                if not hmac.compare_digest(hashlib.sha256(body.encode()).hexdigest(), digest):
                    raise BankError('Stored batch prediction failed integrity check', 503)
            changed = {mid: value for mid, value in candidates.items() if mid not in existing or existing[mid][0] != value[0]}
            if not changed:
                return {'round_id': round_id, 'duplicate': True, 'matches': {mid: {'sha256': value[0]} for mid, value in candidates.items()}}
            phase_row = db.execute('SELECT phase,phase_until FROM round_control WHERE round_id=?', (round_id,)).fetchone()
            current = datetime.now(timezone.utc)
            if not phase_row or current >= utc_time(phase_row[1]) or current >= self.deadline:
                raise BankError('Submission window is closed', 409)
            phase = phase_row[0]
            if phase not in ('first', 'recovery', 'review') or (phase == 'review' and self.round['track'] != 'online'):
                raise BankError('Submission window is closed', 409)
            if any(mid in existing for mid in changed) and not (phase == 'review' and self.round['track'] == 'online'):
                raise BankError('A submitted prediction cannot be changed in this phase', 409)
            effective = {mid: json.loads(row[1]) for mid, row in existing.items()}
            effective.update({mid: json.loads(body) for mid, (_, body) in changed.items()})
            total = sum(Decimal(str(p['winner_bet']['stake'])) + sum(Decimal(str(x)) for x in p['score_bets'].values()) for p in effective.values())
            snapshot = self.round['accounts'][identity]
            loan = any(p['take_loan'] for p in effective.values()) and snapshot.get('loan_eligible', False)
            cap = Decimal(str(snapshot['cap_with_loan'] if loan else snapshot['cap']))
            if total > cap:
                raise BankError('Combined daily stake exceeds the account cap', 409)
            accepted = current.isoformat()
            for mid, (digest, body) in changed.items():
                db.execute('INSERT OR REPLACE INTO batch_submissions VALUES (?,?,?,?,?,?)', (round_id, identity, mid, digest, body, accepted))
                version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM batch_versions WHERE round_id=? AND account_id=? AND match_id=?',
                                     (round_id, identity, mid)).fetchone()[0]
                db.execute('INSERT INTO batch_versions VALUES (?,?,?,?,?,?,?,?)', (round_id, identity, mid, version, digest, body, accepted, phase))
        return {'round_id': round_id, 'duplicate': False, 'matches': {mid: {'sha256': value[0]} for mid, value in candidates.items()}}
