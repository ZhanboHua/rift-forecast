"""Control-side offline replay ledger with an explicit historical clock.

This module does not launch an Agent or expose an HTTP service. Its input
attestation must be produced by a separately reviewed material audit.
"""

import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import market
from .config import BJT
from .history_accounts import INITIAL_CHIPS, create_private_file
import secrets
from .validate import score_options, validate


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp requires a time zone')
    return parsed.astimezone(timezone.utc)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_material_attestation(base, attestation, cutoff, fixtures):
    """Check immutable evidence; semantic approval remains a trusted audit input."""
    base = Path(base).resolve(strict=True)
    sources = attestation.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('Archived source evidence is required')
    verified = {}
    for item in sources:
        required = {'id', 'raw_path', 'raw_sha256', 'capture_utc', 'archive_url', 'memento_datetime'}
        if set(item) != required or not isinstance(item['id'], str) or not item['id'] or item['id'] in verified:
            raise ValueError('Invalid source evidence')
        path = (base / item['raw_path']).resolve(strict=True)
        capture = parse_time(item['capture_utc'])
        archive = urlsplit(item['archive_url'])
        stamp = capture.strftime('%Y%m%d%H%M%S')
        if (not path.is_relative_to(base) or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != item['raw_sha256']
                or capture > cutoff or archive.scheme != 'https' or archive.hostname != 'web.archive.org'
                or not re.match(r'^/web/' + stamp + r'(?:id_)?/', archive.path)
                or parse_time(parsedate_to_datetime(item['memento_datetime']).isoformat()) != capture):
            raise ValueError('Source hash or historical capture evidence failed')
        verified[item['id']] = capture
    files = attestation.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('An attested material file is required')
    for item in files:
        if set(item) != {'path', 'sha256', 'visible_at', 'source_ids'}:
            raise ValueError('Invalid material record')
        file = (base / item['path']).resolve(strict=True)
        visible = parse_time(item['visible_at'])
        refs = item['source_ids']
        if (not file.is_relative_to(base) or not file.is_file() or visible > cutoff
                or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']
                or not isinstance(refs, list) or not refs
                or any(ref not in verified or verified[ref] > visible for ref in refs)):
            raise ValueError('Material failed its cutoff, source, or hash check')
    proofs = attestation.get('fixture_proofs')
    if not isinstance(proofs, list) or len(proofs) != len(fixtures):
        raise ValueError('Each fixture requires a reviewed proof')
    by_id = {item['match_id']: item for item in fixtures}
    seen = set()
    for proof in proofs:
        if set(proof) != {'match_id', 'fixture_sha256', 'source_id', 'known_at', 'review'}:
            raise ValueError('Invalid fixture proof')
        mid, source = proof['match_id'], proof['source_id']
        known = parse_time(proof['known_at'])
        if (mid not in by_id or mid in seen or source not in verified or proof['review'] != 'PASS'
                or proof['fixture_sha256'] != digest(encode(by_id[mid])) or known > cutoff or verified[source] > known):
            raise ValueError('Fixture proof is not valid at cutoff')
        seen.add(mid)
    if seen != set(by_id):
        raise ValueError('Fixture proofs are incomplete')


class HistoricalController:
    track = 'retrospective-offline'
    first_offset = 360

    def __init__(self, root, wall_clock=None):
        self.root = Path(root)
        if self.track == 'retrospective-offline' and (self.root / 'bank/ONLINE_LEDGER').exists():
            raise ValueError('The online ledger requires the live controller')
        self.db_path = self.root / 'bank/accounts.sqlite3'
        if not self.db_path.is_file():
            raise ValueError('Provisioned historical accounts are required')
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='roster'").fetchone():
                raise ValueError('Ledger requires explicit roster migration')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settlement_reviews (
                    day_id TEXT NOT NULL, match_id TEXT NOT NULL, payload TEXT NOT NULL,
                    sha256 TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
                    prepared_at TEXT NOT NULL, confirmed_at TEXT,
                    PRIMARY KEY(day_id,match_id));
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id INTEGER PRIMARY KEY, action TEXT NOT NULL, day_id TEXT NOT NULL,
                    match_id TEXT NOT NULL, happened_at TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS historical_days (
                    day_id TEXT PRIMARY KEY, anchor TEXT NOT NULL, cutoff TEXT NOT NULL,
                    virtual_now TEXT NOT NULL, material_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('prepared','running','locked','settled')),
                    last_wall TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS historical_matches (
                    day_id TEXT NOT NULL, match_id TEXT NOT NULL, fixture TEXT NOT NULL,
                    PRIMARY KEY(day_id,match_id));
                CREATE TABLE IF NOT EXISTS historical_runs (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, first_status TEXT NOT NULL,
                    session_id TEXT, recovery_status TEXT NOT NULL,
                    PRIMARY KEY(day_id,account_id));
                CREATE TABLE IF NOT EXISTS historical_windows (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, phase TEXT NOT NULL,
                    ends_at TEXT NOT NULL, PRIMARY KEY(day_id,account_id));
                CREATE TABLE IF NOT EXISTS historical_recovery_attempts (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                    session_id TEXT NOT NULL, missing_predictions INTEGER NOT NULL,
                    missing_summary INTEGER NOT NULL, started_at TEXT NOT NULL, ends_at TEXT NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY(day_id,account_id,attempt));
                CREATE TABLE IF NOT EXISTS historical_submissions (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL, accepted_at TEXT NOT NULL,
                    phase TEXT NOT NULL, PRIMARY KEY(day_id,account_id,match_id));
                CREATE TABLE IF NOT EXISTS historical_locks (
                    day_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS historical_results (
                    day_id TEXT NOT NULL, match_id TEXT NOT NULL, result_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(day_id,match_id));
                CREATE TABLE IF NOT EXISTS historical_reservations (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, amount REAL NOT NULL,
                    PRIMARY KEY(day_id,account_id));
                CREATE TABLE IF NOT EXISTS historical_account_results (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(day_id,account_id,match_id));
            ''')

    @property
    def accounts(self):
        with self.connect() as db:
            return tuple(row[0] for row in db.execute('SELECT account_id FROM accounts ORDER BY account_id'))

    def _day_accounts(self, db, day_id):
        return tuple(row[0] for row in db.execute(
            'SELECT account_id FROM historical_runs WHERE day_id=? ORDER BY account_id', (day_id,)))

    def change_roster(self, account, action):
        if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', account):
            raise ValueError('Invalid account ID')
        if action not in ('add', 'enable', 'disable'):
            raise ValueError('Invalid roster action')
        token_path = self.root / 'credentials' / (account + '.token')
        created = False
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM historical_days WHERE state!='settled' LIMIT 1").fetchone():
                    raise RuntimeError('Roster changes require all days to be settled')
                old = db.execute('SELECT active FROM roster WHERE account_id=?', (account,)).fetchone()
                when = self.now().isoformat()
                if action == 'add':
                    if old:
                        raise ValueError('Account already exists')
                    token = secrets.token_urlsafe(48)
                    create_private_file(token_path, token + '\n')
                    created = True
                    db.execute('INSERT INTO accounts VALUES (?,?,?,?,?)',
                               (account, hashlib.sha256(token.encode()).hexdigest(), INITIAL_CHIPS, 0, 0))
                    db.execute('INSERT INTO roster VALUES (?,?,?,?)', (account, 1, when, INITIAL_CHIPS))
                else:
                    if not old or old[0] == (action == 'enable'):
                        raise ValueError('Invalid account state')
                    db.execute('UPDATE roster SET active=? WHERE account_id=?', (int(action == 'enable'), account))
                db.execute('INSERT INTO roster_audit(account_id,action,effective_at) VALUES (?,?,?)', (account, action, when))
        except Exception:
            if created:
                token_path.unlink(missing_ok=True)
            raise
        return {'account_id': account, 'action': action, 'effective_at': when}

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    def now(self):
        current = self.wall_clock()
        if current.tzinfo is None:
            raise ValueError('Wall clock requires a time zone')
        return current.astimezone(timezone.utc)

    @staticmethod
    def _day(db, day_id):
        row = db.execute('SELECT anchor,cutoff,virtual_now,state,last_wall FROM historical_days WHERE day_id=?',
                         (day_id,)).fetchone()
        if not row:
            raise ValueError('Unknown historical day')
        return row

    def _wall(self, db, day_id):
        now = self.now()
        if now < parse_time(self._day(db, day_id)[4]):
            raise RuntimeError('Wall clock moved backwards')
        db.execute('UPDATE historical_days SET last_wall=? WHERE day_id=?', (now.isoformat(), day_id))
        return now

    def workspace_dates(self, day_id):
        with self.connect() as db:
            anchor = self._day(db, day_id)[0]
            earlier = [row[0] for row in db.execute("SELECT anchor FROM historical_days WHERE anchor<? AND state='settled'", (anchor,))]
        days = [parse_time(value).astimezone(BJT).strftime('%Y%m%d') for value in (anchor, *earlier)]
        return days[0], set(days[1:])

    def authenticate(self, token):
        if not isinstance(token, str) or not token or len(token) > 512:
            return None
        wanted = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            rows = db.execute('SELECT account_id,token_sha256 FROM accounts').fetchall()
        return next((account for account, expected in rows if hmac.compare_digest(wanted, expected)), None)

    def account_view(self, day_id, account):
        if account not in self.accounts:
            raise PermissionError('Unknown account')
        with self.connect() as db:
            db.execute('BEGIN')
            anchor, _, virtual_now, _, _ = self._day(db, day_id)
            if account not in self._day_accounts(db, day_id):
                raise PermissionError('Account was not in this day')
            if db.execute('SELECT 1 FROM historical_days WHERE anchor>? LIMIT 1', (anchor,)).fetchone():
                raise RuntimeError('Account state is available for the latest historical day only')
            accounts = {name: (balance, debt, loans) for name, balance, debt, loans in db.execute(
                'SELECT account_id,balance,debt,loans_taken FROM accounts')}
            accounts = {name: values for name, values in accounts.items() if name in self._day_accounts(db, day_id)}
            balance, debt, loans = accounts[account]
            reservation = db.execute('SELECT amount FROM historical_reservations WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()
            if reservation:
                reserved = reservation[0]
            else:
                rows = db.execute('SELECT payload,sha256 FROM historical_submissions WHERE day_id=? AND account_id=?', (day_id, account)).fetchall()
                if any(not hmac.compare_digest(digest(body), sha) for body, sha in rows):
                    raise RuntimeError('Submission integrity failed')
                bets = [json.loads(body) for body, _ in rows]
                reserved = sum(bet['winner_bet']['stake'] + sum(bet['score_bets'].values()) for bet in bets)
            available = max(0.0, round(balance - reserved, 2))
            eligible = market.loan_eligible(balance, loans)
            cap = market.stake_cap(balance, reserved)
            history = []
            for mid, body, checksum, bet, bet_hash in db.execute('''
                    SELECT r.match_id,r.payload,r.sha256,s.payload,s.sha256
                    FROM historical_account_results AS r
                    JOIN historical_days AS d ON d.day_id=r.day_id
                    LEFT JOIN historical_submissions AS s
                        ON (s.day_id,s.account_id,s.match_id)=(r.day_id,r.account_id,r.match_id)
                    WHERE r.account_id=? AND d.anchor<=?
                    ORDER BY d.anchor,r.match_id''', (account, anchor)):
                if digest(body) != checksum or (bet is not None and digest(bet) != bet_hash):
                    raise RuntimeError('Account history integrity failed')
                result = json.loads(body)
                if parse_time(result['observed_at']) > parse_time(virtual_now):
                    continue
                prediction = json.loads(bet) if bet is not None else {}
                history.append({'match_id': mid, 'status': result['status'], 'pnl': result['pnl'],
                                **{key: prediction[key] for key in ('p_team_a_win', 'winner_bet', 'score_bets') if key in prediction}})
            metadata = {r[0]: {'joined_at': r[1], 'initial_chips': r[2], 'settled_count': 0} for r in db.execute('SELECT account_id,joined_at,initial_chips FROM roster') if r[0] in accounts}
            for name, body in db.execute('SELECT account_id,payload FROM historical_account_results'):
                if name in metadata and parse_time(json.loads(body)['observed_at']) <= parse_time(virtual_now):
                    metadata[name]['settled_count'] += 1
            return {'account_id': account, 'track': self.track, 'round_id': day_id,
                    'balance': balance, 'debt': debt, 'net': round(balance - debt, 2),
                    'available': available, 'reserved': reserved, 'open_stakes': reserved, 'cap': cap,
                    'cap_with_loan': market.stake_cap(balance + market.loan_terms()['principal'], reserved) if eligible else cap,
                    'loan_eligible': eligible, 'loans_taken': loans, 'history': history,
                    'standings': [{**row, **metadata[row['account_id']]} for row in market.standings({name: round(chips - owed, 2) for name, (chips, owed, _) in accounts.items()})]}

    def match_view(self, day_id, account):
        if account not in self.accounts:
            raise PermissionError('Unknown account')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self._wall(db, day_id)
            anchor, _, _, state, _ = self._day(db, day_id)
            window = db.execute('SELECT ends_at FROM historical_windows WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()
            if state != 'running' or not window or now >= parse_time(window[0]):
                raise RuntimeError('Daily fixtures are not available')
            fixtures = [json.loads(row[0]) for row in db.execute(
                'SELECT fixture FROM historical_matches WHERE day_id=? ORDER BY match_id', (day_id,))]
            fields = ('match_id', 'team_a', 'team_b', 'best_of')
            return {'round_id': day_id, 'track': self.track,
                    'deadline': (parse_time(anchor) - timedelta(minutes=10)).isoformat(),
                    'matches': [{**{key: fixture[key] for key in fields}, 'start': parse_time(fixture['start']).isoformat()}
                                for fixture in fixtures]}

    def receipt_view(self, day_id, account):
        if account not in self.accounts:
            raise PermissionError('Unknown account')
        with self.connect() as db:
            state = self._day(db, day_id)[3]
            run = db.execute('SELECT first_status FROM historical_runs WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()
            if state == 'prepared' or not run or run[0] == 'pending':
                raise RuntimeError('Daily fixtures are not available')
            fixtures = [row[0] for row in db.execute('SELECT match_id FROM historical_matches WHERE day_id=?', (day_id,))]
            submitted = {mid: sha for mid, sha in db.execute(
                'SELECT match_id,sha256 FROM historical_submissions WHERE day_id=? AND account_id=?', (day_id, account))}
            projections = {}
            for mid, body, sha in db.execute('SELECT match_id,payload,sha256 FROM historical_account_results WHERE day_id=? AND account_id=?',
                                             (day_id, account)):
                if not hmac.compare_digest(digest(body), sha):
                    raise RuntimeError('Result projection integrity failed')
                projections[mid] = json.loads(body)
            matches = {}
            for mid in fixtures:
                if mid in projections:
                    matches[mid] = {'submitted': mid in submitted, 'state': 'settled', 'result': projections[mid]}
                elif mid in submitted:
                    matches[mid] = {'submitted': True, 'sha256': submitted[mid], 'state': 'sealed_not_settled' if state != 'running' else 'accepted_not_settled'}
                else:
                    matches[mid] = {'submitted': False, 'state': 'abstained' if state in ('locked', 'settled') else 'open'}
            return {'round_id': day_id, 'matches': matches}

    def validate_material(self, material_root, attestation, cutoff, fixtures):
        if (attestation.get('state') != 'VERIFIED_AS_OF' or attestation.get('english_review') != 'PASS'
                or attestation.get('provenance_review') != 'PASS' or parse_time(attestation['cutoff']) != cutoff):
            raise ValueError('Verified English material attestation is required')
        validate_material_attestation(material_root, attestation, cutoff, fixtures)

    def prepare_day(self, day_id, anchor, anchor_known_at, fixtures, material_root, attestation):
        anchor_time = parse_time(anchor)
        cutoff = anchor_time - timedelta(minutes=400)
        if parse_time(anchor_known_at) > cutoff or not fixtures:
            raise ValueError('The day anchor must be known by T-400')
        if any(set(item) != {'match_id', 'team_a', 'team_b', 'best_of', 'start', 'announced_at'} for item in fixtures):
            raise ValueError('Fixture contains unsupported fields')
        ids = set()
        local_date = anchor_time.astimezone(BJT).date()
        for item in fixtures:
            match_id = item['match_id']
            if (not isinstance(match_id, str) or not match_id or match_id in ids
                    or not all(isinstance(item[x], str) and item[x] for x in ('team_a', 'team_b'))
                    or item['team_a'] == item['team_b'] or type(item['best_of']) is not int or item['best_of'] not in (1, 3, 5)
                    or parse_time(item['announced_at']) > cutoff
                    or parse_time(item['start']).astimezone(BJT).date() != local_date):
                raise ValueError('Invalid or unavailable fixture')
            ids.add(match_id)
        if min(parse_time(item['start']) for item in fixtures) != anchor_time:
            raise ValueError('Anchor must be the earliest match')
        self.validate_material(material_root, attestation, cutoff, fixtures)
        attestation_hash = digest(encode(attestation))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM historical_days WHERE state!='settled' LIMIT 1").fetchone():
                raise RuntimeError('Another historical day is not settled')
            previous_anchors = [parse_time(row[0]) for row in db.execute('SELECT anchor FROM historical_days')]
            if previous_anchors and anchor_time <= max(previous_anchors):
                raise RuntimeError('Historical days must advance chronologically')
            for body, sha in db.execute('SELECT payload,sha256 FROM historical_results'):
                if not hmac.compare_digest(digest(body), sha):
                    raise RuntimeError('Prior result integrity failed')
                if parse_time(json.loads(body)['observed_at']) > cutoff:
                    raise RuntimeError('A prior account result is not visible by the next T-400 cutoff')
            db.execute('INSERT INTO historical_days VALUES (?,?,?,?,?,?,?)',
                       (day_id, anchor_time.isoformat(), cutoff.isoformat(),
                        (anchor_time - timedelta(minutes=self.first_offset)).isoformat(), attestation_hash, 'prepared', self.now().isoformat()))
            for item in fixtures:
                db.execute('INSERT INTO historical_matches VALUES (?,?,?)', (day_id, item['match_id'], encode(item)))
            active = [r[0] for r in db.execute('SELECT account_id FROM roster WHERE active=1 ORDER BY account_id')]
            if not active:
                raise RuntimeError('At least one active account is required')
            for account in active:
                db.execute('INSERT INTO historical_runs VALUES (?,?,?,?,?)', (day_id, account, 'pending', None, 'pending'))

    def _window_end(self, db, day_id, now, phase, seconds):
        return now + timedelta(seconds=seconds)

    def _revisions_allowed(self, phase):
        return False

    def _record_revision(self, db, day_id, account, match_id, body, sha, when, phase):
        pass

    def _review_status(self, db, day_id, account):
        return None

    def _check_lock_time(self, db, day_id, now):
        pass

    def open_first(self, day_id, account, seconds=2700):
        if account not in self.accounts or type(seconds) is not int or not 1 <= seconds <= 3600:
            raise ValueError('Invalid account or initial window')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self._wall(db, day_id)
            if self._day(db, day_id)[3] not in ('prepared', 'running'):
                raise RuntimeError('Day is not open')
            status = db.execute('SELECT first_status FROM historical_runs WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()
            if not status or status[0] != 'pending':
                raise RuntimeError('Initial run already started')
            db.execute('UPDATE historical_runs SET first_status=? WHERE day_id=? AND account_id=?', ('running', day_id, account))
            ends = self._window_end(db, day_id, now, 'first', seconds)
            db.execute('INSERT INTO historical_windows VALUES (?,?,?,?)', (day_id, account, 'first', ends.isoformat()))
            db.execute("UPDATE historical_days SET state='running' WHERE day_id=?", (day_id,))

    def finish_run(self, day_id, account, phase, session_id, status, *, verified_continuation=False):
        if phase not in ('first', 'recovery') or status not in ('done', 'U'):
            raise ValueError('Invalid run outcome')
        if verified_continuation and (phase != 'recovery' or status != 'done'):
            raise ValueError('Invalid continuation outcome')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._wall(db, day_id)
            run = db.execute('SELECT first_status,session_id,recovery_status FROM historical_runs WHERE day_id=? AND account_id=?',
                             (day_id, account)).fetchone()
            window = db.execute('SELECT phase FROM historical_windows WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()
            if not run or not window or window[0] != phase or self._day(db, day_id)[3] != 'running':
                raise RuntimeError('Run phase is not active')
            if phase == 'first':
                if status == 'done' and not session_id:
                    raise ValueError('Completed run requires a session')
                db.execute('UPDATE historical_runs SET first_status=?,session_id=? WHERE day_id=? AND account_id=?',
                           (status, session_id, day_id, account))
            else:
                if not run[1] or session_id != run[1]:
                    raise ValueError('Recovery must use the original session')
                attempt = db.execute("SELECT missing_predictions FROM historical_recovery_attempts WHERE day_id=? AND account_id=? AND status='running'",
                                     (day_id, account)).fetchone()
                db.execute("UPDATE historical_recovery_attempts SET status=? WHERE day_id=? AND account_id=? AND status='running'",
                           (status, day_id, account))
                successful = db.execute("SELECT 1 FROM historical_recovery_attempts WHERE day_id=? AND account_id=? AND status='done' LIMIT 1",
                                        (day_id, account)).fetchone()
                missing = db.execute('''SELECT (SELECT COUNT(*) FROM historical_matches WHERE day_id=?) -
                    (SELECT COUNT(*) FROM historical_submissions WHERE day_id=? AND account_id=?)''',
                                     (day_id, day_id, account)).fetchone()[0]
                keep_success = successful and attempt and attempt[0] == missing
                db.execute('UPDATE historical_runs SET first_status=?,recovery_status=? WHERE day_id=? AND account_id=?',
                           ('done' if verified_continuation else run[0], 'done' if keep_success else status, day_id, account))
            db.execute('DELETE FROM historical_windows WHERE day_id=? AND account_id=?', (day_id, account))

    def open_recovery(self, day_id, account, session_id, seconds=1800, *, missing_summary=False):
        if type(seconds) is not int or not 1 <= seconds <= 1800 or type(missing_summary) is not bool:
            raise ValueError('Invalid recovery window')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self._wall(db, day_id)
            run = db.execute('SELECT first_status,session_id,recovery_status FROM historical_runs WHERE day_id=? AND account_id=?',
                             (day_id, account)).fetchone()
            expected = db.execute('SELECT COUNT(*) FROM historical_matches WHERE day_id=?', (day_id,)).fetchone()[0]
            actual = db.execute('SELECT COUNT(*) FROM historical_submissions WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()[0]
            attempts = db.execute('SELECT COALESCE(MAX(attempt),0) FROM historical_recovery_attempts WHERE day_id=? AND account_id=?',
                                  (day_id, account)).fetchone()[0]
            if not attempts and run and run[2] in ('done', 'U'):
                attempts = 1
            if (self._day(db, day_id)[3] != 'running' or not run or run[0] not in ('done', 'U')
                    or not session_id or run[1] != session_id or run[2] == 'running' or attempts >= 2
                    or (actual >= expected and not missing_summary)):
                raise RuntimeError('Recovery is not permitted')
            ends_at = self._window_end(db, day_id, now, 'recovery', seconds).isoformat()
            db.execute('INSERT INTO historical_recovery_attempts VALUES (?,?,?,?,?,?,?,?,?)',
                       (day_id, account, attempts + 1, session_id, expected - actual, missing_summary,
                        now.isoformat(), ends_at, 'running'))
            db.execute("UPDATE historical_runs SET recovery_status='running' WHERE day_id=? AND account_id=?", (day_id, account))
            db.execute('INSERT INTO historical_windows VALUES (?,?,?,?)', (day_id, account, 'recovery', ends_at))

    def submit(self, day_id, token, prediction):
        return self.submit_many(day_id, token, [prediction])

    def submit_many(self, day_id, token, predictions):
        account = self.authenticate(token)
        if account is None:
            raise PermissionError('Invalid account credential')
        if not isinstance(predictions, list) or not 1 <= len(predictions) <= 30:
            raise ValueError('A nonempty prediction batch is required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self._wall(db, day_id)
            window = db.execute('SELECT phase,ends_at FROM historical_windows WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()
            if self._day(db, day_id)[3] != 'running' or not window or now >= parse_time(window[1]):
                raise RuntimeError('Submission window is closed')
            existing_rows = db.execute('SELECT match_id,payload,sha256 FROM historical_submissions WHERE day_id=? AND account_id=?',
                                       (day_id, account)).fetchall()
            existing = {}
            for match_id, body, sha in existing_rows:
                if not hmac.compare_digest(digest(body), sha):
                    raise RuntimeError('Stored submission integrity failed')
                existing[match_id] = (body, sha)
            candidates = {}
            for prediction in predictions:
                match_id = prediction.get('match_id') if isinstance(prediction, dict) else None
                if match_id in candidates:
                    raise ValueError('Duplicate fixture in prediction batch')
                row = db.execute('SELECT fixture FROM historical_matches WHERE day_id=? AND match_id=?', (day_id, match_id)).fetchone()
                if not row:
                    raise ValueError('Fixture is not visible')
                fixture = json.loads(row[0])
                clean, errors = validate(prediction, match_id, fixture['team_a'], fixture['team_b'], fixture['best_of'])
                if errors:
                    raise ValueError('Invalid historical prediction')
                body = encode(clean)
                if token in body:
                    raise ValueError('Prediction contains an account credential')
                if (match_id in existing and not hmac.compare_digest(existing[match_id][1], digest(body))
                        and not self._revisions_allowed(window[0])):
                    raise RuntimeError('Offline prediction cannot be changed')
                candidates[match_id] = (body, digest(body))
            changed = {mid: item for mid, item in candidates.items() if mid not in existing or item != existing[mid]}
            if not changed:
                return {'duplicate': True, 'matches': {mid: {'sha256': item[1]} for mid, item in candidates.items()}}
            bets = [json.loads(item[0]) for item in {**existing, **changed}.values()]
            balance, loans = db.execute('SELECT balance,loans_taken FROM accounts WHERE account_id=?', (account,)).fetchone()
            if any(Decimal(str(value)).normalize().as_tuple().exponent < -2 for bet in bets
                   for value in [bet['winner_bet']['stake'], *bet['score_bets'].values()]):
                raise ValueError('Stake requires at most two decimal places')
            total = sum(Decimal(str(bet['winner_bet']['stake'])) + sum(Decimal(str(x)) for x in bet['score_bets'].values()) for bet in bets)
            loan = any(bet['take_loan'] for bet in bets) and market.loan_eligible(balance, loans)
            cap = Decimal(str(balance + (market.loan_terms()['principal'] if loan else 0)))
            if total > cap:
                raise ValueError('Combined daily stakes exceed available chips')
            for match_id, (body, sha) in changed.items():
                db.execute('INSERT OR REPLACE INTO historical_submissions VALUES (?,?,?,?,?,?,?)',
                           (day_id, account, match_id, body, sha, now.isoformat(), window[0]))
                self._record_revision(db, day_id, account, match_id, body, sha, now.isoformat(), window[0])
            return {'duplicate': False, 'matches': {mid: {'sha256': item[1]} for mid, item in candidates.items()}}

    def seal_day(self, day_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._check_lock_time(db, day_id, self._wall(db, day_id))
            anchor, _, _, state, _ = self._day(db, day_id)
            if state != 'running':
                raise RuntimeError('Day is not running')
            matches = {mid: json.loads(body) for mid, body in db.execute(
                'SELECT match_id,fixture FROM historical_matches WHERE day_id=?', (day_id,))}
            locked = {}
            for account in self._day_accounts(db, day_id):
                first, session, recovery = db.execute(
                    'SELECT first_status,session_id,recovery_status FROM historical_runs WHERE day_id=? AND account_id=?',
                    (day_id, account)).fetchone()
                if first not in ('done', 'U') or recovery == 'running':
                    raise RuntimeError('An Agent run is not finished')
                rows = db.execute('SELECT match_id,payload,sha256,phase FROM historical_submissions WHERE day_id=? AND account_id=?',
                                  (day_id, account)).fetchall()
                saved = {mid: (body, sha, phase) for mid, body, sha, phase in rows}
                review = self._review_status(db, day_id, account)
                if review == 'running':
                    raise RuntimeError('An online review is not finished')
                if self.track == 'retrospective-offline' and len(saved) < len(matches) and session and recovery == 'pending':
                    raise RuntimeError('Missing submissions require one recovery')
                entries = {}
                for mid in matches:
                    if mid not in saved:
                        entries[mid] = {'status': 'abstain', 'execution': 'U' if 'U' in (first, recovery, review) else 'done'}
                        continue
                    body, sha, phase = saved[mid]
                    if not hmac.compare_digest(digest(body), sha):
                        raise RuntimeError('Submission integrity check failed')
                    phase_status = {'first': first, 'recovery': recovery, 'review': review}
                    verified = phase_status.get(phase) == 'done'
                    entries[mid] = {'status': 'ok' if verified else 'U', 'prediction': json.loads(body), 'sha256': sha}
                locked[account] = entries
            for account, entries in locked.items():
                balance, debt, loans = db.execute('SELECT balance,debt,loans_taken FROM accounts WHERE account_id=?', (account,)).fetchone()
                bets = [item['prediction'] for item in entries.values() if item['status'] == 'ok']
                if any(bet['take_loan'] for bet in bets) and market.loan_eligible(balance, loans):
                    terms = market.loan_terms()
                    balance += terms['principal']
                    debt += terms['repay']
                    loans += 1
                    db.execute('UPDATE accounts SET balance=?,debt=?,loans_taken=? WHERE account_id=?', (balance, debt, loans, account))
                reserved = sum(bet['winner_bet']['stake'] + sum(bet['score_bets'].values()) for bet in bets)
                if reserved > balance + 1e-8:
                    raise RuntimeError('Locked stakes exceed available chips')
                db.execute('INSERT INTO historical_reservations VALUES (?,?,?)', (day_id, account, reserved))
            body = encode({'day_id': day_id, 'matches': matches, 'entries': locked})
            db.execute('INSERT INTO historical_locks VALUES (?,?,?)', (day_id, body, digest(body)))
            db.execute('DELETE FROM historical_windows WHERE day_id=?', (day_id,))
            db.execute("UPDATE historical_days SET state='locked',virtual_now=? WHERE day_id=?",
                       ((parse_time(anchor) - timedelta(minutes=10)).isoformat(), day_id))
            return {'state': 'locked', 'sha256': digest(body)}

    def advance_clock(self, day_id, value):
        target = parse_time(value)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._wall(db, day_id)
            _, _, previous, state, _ = self._day(db, day_id)
            if state not in ('locked', 'settled') or target < parse_time(previous):
                raise RuntimeError('Historical clock cannot advance before locking or move backwards')
            db.execute('UPDATE historical_days SET virtual_now=? WHERE day_id=?', (target.isoformat(), day_id))

    def prepare_settlement(self, day_id, match_id, score, result_id, source, published_at, evidence):
        payload = {'match_id': match_id, 'score': score, 'result_id': result_id,
                   'source': source, 'published_at': published_at, 'evidence': evidence}
        body = encode(payload)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._day(db, day_id)
            row = db.execute('SELECT fixture FROM historical_matches WHERE day_id=? AND match_id=?',
                             (day_id, match_id)).fetchone()
            if not row or score not in score_options(json.loads(row[0])['best_of']):
                raise ValueError('Unknown match or invalid score')
            existing = db.execute('SELECT payload,status FROM settlement_reviews WHERE day_id=? AND match_id=?',
                                  (day_id, match_id)).fetchone()
            if existing:
                if existing[0] == body:
                    return {'status': existing[1], 'duplicate': True, 'sha256': digest(body)}
                db.execute("UPDATE settlement_reviews SET reason='conflicting candidate' WHERE day_id=? AND match_id=?", (day_id, match_id))
                db.execute('INSERT INTO admin_audit(action,day_id,match_id,happened_at,detail) VALUES (?,?,?,?,?)',
                           ('conflict', day_id, match_id, self.now().isoformat(), digest(body)))
                return {'status': 'conflict', 'duplicate': False}
            reason = None
            if not result_id or not source or not isinstance(evidence, dict) or set(evidence) != {'path', 'sha256', 'observed_at'}:
                reason = 'missing evidence or source'
            else:
                try:
                    parse_time(published_at)
                    parse_time(evidence['observed_at'])
                    root = (self.root / 'result-evidence').resolve(strict=True)
                    proof = (root / evidence['path']).resolve(strict=True)
                    if not proof.is_relative_to(root) or digest(proof.read_text()) != evidence['sha256']:
                        reason = 'missing or mismatched evidence'
                except (OSError, ValueError, TypeError, KeyError):
                    reason = 'missing or invalid evidence'
            now = self.now().isoformat()
            db.execute('INSERT INTO settlement_reviews VALUES (?,?,?,?,?,?,?,NULL)',
                       (day_id, match_id, body, digest(body), 'pending', reason, now))
            db.execute('INSERT INTO admin_audit(action,day_id,match_id,happened_at,detail) VALUES (?,?,?,?,?)',
                       ('prepare', day_id, match_id, now, digest(body)))
            return {'status': 'pending', 'reason': reason, 'sha256': digest(body)}

    def review_status(self, day_id, match_id):
        with self.connect() as db:
            row = db.execute('SELECT payload,sha256,status,reason,prepared_at,confirmed_at FROM settlement_reviews WHERE day_id=? AND match_id=?', (day_id, match_id)).fetchone()
        if not row or digest(row[0]) != row[1]:
            raise ValueError('Review not found or integrity failed')
        return {'candidate': json.loads(row[0]), 'sha256': row[1], 'status': row[2],
                'reason': row[3], 'prepared_at': row[4], 'confirmed_at': row[5]}

    def dismiss_review(self, day_id, match_id, expected_sha256):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT sha256,status FROM settlement_reviews WHERE day_id=? AND match_id=?', (day_id, match_id)).fetchone()
            if not row or row[0] != expected_sha256 or row[1] != 'pending':
                raise ValueError('Pending review not found or changed')
            db.execute('DELETE FROM settlement_reviews WHERE day_id=? AND match_id=?', (day_id, match_id))
            db.execute('INSERT INTO admin_audit(action,day_id,match_id,happened_at,detail) VALUES (?,?,?,?,?)',
                       ('dismiss', day_id, match_id, self.now().isoformat(), expected_sha256))
        return {'status': 'dismissed'}

    def confirm_settlement(self, day_id, match_id, expected_sha256):
        with self.connect() as db:
            row = db.execute('SELECT payload,sha256,status,reason FROM settlement_reviews WHERE day_id=? AND match_id=?',
                             (day_id, match_id)).fetchone()
        if not row or row[1] != expected_sha256 or digest(row[0]) != row[1]:
            raise ValueError('Pending review not found or changed')
        if row[2] == 'confirmed':
            return {'duplicate': True, 'status': 'confirmed'}
        if row[3]:
            raise RuntimeError('Pending evidence requires review')
        data = json.loads(row[0])
        try:
            return self._settle(day_id, **data, review_sha256=expected_sha256)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            with self.connect() as db:
                db.execute('INSERT INTO admin_audit(action,day_id,match_id,happened_at,detail) VALUES (?,?,?,?,?)',
                           ('confirm_failed', day_id, match_id, self.now().isoformat(), str(exc)[:300]))
            raise

    def _settle(self, day_id, match_id, score, result_id, source, published_at, evidence, review_sha256):
        if not result_id or not source:
            raise ValueError('A result identifier and source are required')
        published = parse_time(published_at)
        if not isinstance(evidence, dict) or set(evidence) != {'path', 'sha256', 'observed_at'}:
            raise ValueError('Result publication evidence is required')
        evidence_root = (self.root / 'result-evidence').resolve(strict=True)
        proof = (evidence_root / evidence['path']).resolve(strict=True)
        observed = parse_time(evidence['observed_at'])
        if (not proof.is_relative_to(evidence_root) or not proof.is_file()
                or hashlib.sha256(proof.read_bytes()).hexdigest() != evidence['sha256'] or observed < published):
            raise ValueError('Result evidence failed validation')
        record = json.loads(proof.read_text())
        expected = {'match_id': match_id, 'score': score, 'result_id': result_id, 'source': source,
                    'published_at': published.isoformat(), 'observed_at': observed.isoformat(), 'status': 'VERIFIED'}
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError('Result evidence does not bind the declared result')
        raw = (evidence_root / record['raw_path']).resolve(strict=True)
        if (not raw.is_relative_to(evidence_root) or not raw.is_file()
                or hashlib.sha256(raw.read_bytes()).hexdigest() != record.get('raw_sha256')):
            raise ValueError('Result source content failed validation')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            review = db.execute('SELECT payload,sha256,status,reason FROM settlement_reviews WHERE day_id=? AND match_id=?', (day_id, match_id)).fetchone()
            if not review or review[1] != review_sha256 or digest(review[0]) != review_sha256 or review[3]:
                raise RuntimeError('Review changed or is blocked')
            if review[2] == 'confirmed':
                return {'duplicate': True, 'status': 'confirmed'}
            # Verify the evidence again while the settlement transaction is held.
            if hashlib.sha256(proof.read_bytes()).hexdigest() != evidence['sha256'] or hashlib.sha256(raw.read_bytes()).hexdigest() != record.get('raw_sha256'):
                raise ValueError('Result evidence changed')
            self._wall(db, day_id)
            _, _, virtual_now, state, _ = self._day(db, day_id)
            row = db.execute('SELECT payload,sha256 FROM historical_locks WHERE day_id=?', (day_id,)).fetchone()
            if state not in ('locked', 'settled') or not row or not hmac.compare_digest(digest(row[0]), row[1]):
                raise RuntimeError('All predictions must be locked before settlement')
            lock = json.loads(row[0])
            match = lock['matches'].get(match_id)
            if (not match or score not in score_options(match['best_of']) or published < parse_time(match['start'])
                    or observed > parse_time(virtual_now)):
                raise ValueError('Result is invalid or not yet visible')
            body = encode({'score': score, 'result_id': result_id, 'source': source,
                           'published_at': published.isoformat(), 'observed_at': observed.isoformat(),
                           'evidence_sha256': evidence['sha256']})
            existing = db.execute('SELECT payload,sha256 FROM historical_results WHERE day_id=? AND match_id=?', (day_id, match_id)).fetchone()
            if existing:
                if hmac.compare_digest(digest(existing[0]), existing[1]) and existing[0] == body:
                    return {'duplicate': True}
                raise RuntimeError('Conflicting historical result')
            if any(item['status'] == 'U' or item.get('execution') == 'U'
                   for entries in lock['entries'].values() for item in entries.values()):
                raise RuntimeError('An unverified Agent run prevents settlement')
            previous = db.execute('SELECT payload,sha256 FROM historical_results WHERE day_id=?', (day_id,)).fetchall()
            for old_body, old_hash in previous:
                if not hmac.compare_digest(digest(old_body), old_hash):
                    raise RuntimeError('Historical result integrity failed')
                if parse_time(json.loads(old_body)['observed_at']) > observed:
                    raise RuntimeError('Results must be injected in publication order')
            winner = match['team_a'] if int(score.split('-')[0]) > int(score.split('-')[1]) else match['team_b']
            winner_stakes, score_stakes = {}, {}
            for account, entries in lock['entries'].items():
                item = entries[match_id]
                if item['status'] == 'ok':
                    prediction = item['prediction']
                    winner_stakes[account] = (prediction['winner_bet']['team'], prediction['winner_bet']['stake'])
                    score_stakes[account] = prediction['score_bets']
                else:
                    winner_stakes[account], score_stakes[account] = (None, 0), {}
            winner_payouts, _ = market.parimutuel(winner_stakes, winner)
            score_payouts, _ = market.parimutuel(score_stakes, score)
            for account in lock['entries']:
                pnl = winner_payouts[account]['pnl'] + score_payouts[account]['pnl']
                staked = winner_payouts[account]['staked'] + score_payouts[account]['staked']
                balance = db.execute('SELECT balance FROM accounts WHERE account_id=?', (account,)).fetchone()[0]
                reserved = db.execute('SELECT amount FROM historical_reservations WHERE day_id=? AND account_id=?', (day_id, account)).fetchone()[0]
                if reserved + 1e-8 < staked:
                    raise RuntimeError('Reservation integrity failed')
                db.execute('UPDATE accounts SET balance=? WHERE account_id=?', (round(balance + pnl, 2), account))
                db.execute('UPDATE historical_reservations SET amount=? WHERE day_id=? AND account_id=?',
                           (round(reserved - staked, 2), day_id, account))
                entry = lock['entries'][account][match_id]
                probability = entry['prediction']['p_team_a_win'] if entry['status'] == 'ok' else 0.5
                projection = encode({'status': entry['status'], 'score': score, 'pnl': round(pnl, 2),
                                     'brier': round(market.brier(probability, winner == match['team_a']), 6),
                                     'log_loss': round(market.log_loss(probability, winner == match['team_a']), 6),
                                     'result_id': result_id, 'observed_at': observed.isoformat()})
                db.execute('INSERT INTO historical_account_results VALUES (?,?,?,?,?)',
                           (day_id, account, match_id, projection, digest(projection)))
            db.execute('INSERT INTO historical_results VALUES (?,?,?,?,?)', (day_id, match_id, result_id, body, digest(body)))
            count = db.execute('SELECT COUNT(*) FROM historical_results WHERE day_id=?', (day_id,)).fetchone()[0]
            if count == len(lock['matches']):
                db.execute("UPDATE historical_days SET state='settled' WHERE day_id=?", (day_id,))
            confirmed = self.now().isoformat()
            db.execute("UPDATE settlement_reviews SET status='confirmed',confirmed_at=? WHERE day_id=? AND match_id=?", (confirmed, day_id, match_id))
            db.execute('INSERT INTO admin_audit(action,day_id,match_id,happened_at,detail) VALUES (?,?,?,?,?)',
                       ('confirm', day_id, match_id, confirmed, review_sha256))
            return {'duplicate': False, 'state': 'settled' if count == len(lock['matches']) else 'locked'}
