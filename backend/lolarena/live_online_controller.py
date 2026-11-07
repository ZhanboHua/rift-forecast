"""Wall-clock online match days using an independent Bank ledger."""
import hashlib
from datetime import timedelta
from pathlib import Path

from .historical_controller import HistoricalController, digest, encode, parse_time


class LiveOnlineController(HistoricalController):
    track = 'online'
    first_offset = 300
    windows = {'first': (300, 240), 'recovery': (300, 180), 'review': (180, 135)}

    def __init__(self, root, wall_clock=None):
        if not (Path(root) / 'bank/ONLINE_LEDGER').is_file():
            raise ValueError('An independent online ledger is required')
        super().__init__(root, wall_clock)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS online_reviews (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(day_id,account_id));
                CREATE TABLE IF NOT EXISTS online_review_retries (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, opened_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(day_id,account_id));
                CREATE TABLE IF NOT EXISTS online_submission_versions (
                    day_id TEXT NOT NULL, account_id TEXT NOT NULL, match_id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    accepted_at TEXT NOT NULL, phase TEXT NOT NULL,
                    PRIMARY KEY(day_id,account_id,match_id,version));
            ''')

    def validate_material(self, material_root, attestation, cutoff, fixtures):
        if (attestation.get('state') != 'VERIFIED_LIVE_SNAPSHOT'
                or parse_time(attestation['cutoff']) != cutoff
                or attestation.get('fixtures_sha256') != digest(encode(fixtures))):
            raise ValueError('The online snapshot must bind the approved fixtures and cutoff')
        base = Path(material_root).resolve(strict=True)
        if not attestation.get('files'):
            raise ValueError('Approved material files are required')
        names = set()
        for record in attestation['files']:
            file = (base / record['path']).resolve(strict=True)
            if (not file.is_relative_to(base) or not file.is_file() or record['path'] in names
                    or parse_time(record['visible_at']) > cutoff
                    or hashlib.sha256(file.read_bytes()).hexdigest() != record['sha256']):
                raise ValueError('Online material failed time, path or hash verification')
            names.add(record['path'])

    def _window_end(self, db, day_id, now, phase, seconds):
        anchor = parse_time(self._day(db, day_id)[0])
        start, end = (anchor - timedelta(minutes=x) for x in self.windows[phase])
        if not start <= now < end:
            raise RuntimeError('The online phase is outside its wall-clock window')
        return min(now + timedelta(seconds=seconds), end)

    def _check_lock_time(self, db, day_id, now):
        if now < parse_time(self._day(db, day_id)[0]) - timedelta(minutes=10):
            raise RuntimeError('The online lock time has not arrived')

    def _review_status(self, db, day_id, account):
        row = db.execute('SELECT status FROM online_reviews WHERE day_id=? AND account_id=?',
                         (day_id, account)).fetchone()
        return row[0] if row else None

    def open_review(self, day_id, account, session_id, seconds=2700, *, retry=False):
        if account not in self.accounts or type(seconds) is not int or not 1 <= seconds <= 2700:
            raise ValueError('Invalid review window')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self._wall(db, day_id)
            anchor, _, _, state, _ = self._day(db, day_id)
            run = db.execute('SELECT first_status,session_id,recovery_status FROM historical_runs WHERE day_id=? AND account_id=?',
                             (day_id, account)).fetchone()
            if (state != 'running' or not run or not session_id or run[1] != session_id or run[0] not in ('done', 'U')
                    or run[2] == 'running' or self._review_status(db, day_id, account) != ('U' if retry else None)):
                raise RuntimeError('Review must use an eligible original session')
            end = self._window_end(db, day_id, now, 'review', seconds)
            if retry:
                if db.execute('SELECT 1 FROM online_review_retries WHERE day_id=? AND account_id=?',
                              (day_id, account)).fetchone():
                    raise RuntimeError('The review retry was already used')
                db.execute('INSERT INTO online_review_retries VALUES (?,?,?,?,?)',
                           (day_id, account, now.isoformat(), end.isoformat(), 'running'))
                db.execute("UPDATE online_reviews SET status='running' WHERE day_id=? AND account_id=?",
                           (day_id, account))
            else:
                db.execute('INSERT INTO online_reviews VALUES (?,?,?)', (day_id, account, 'running'))
            db.execute('INSERT INTO historical_windows VALUES (?,?,?,?)',
                       (day_id, account, 'review', end.isoformat()))
            db.execute('UPDATE historical_days SET virtual_now=? WHERE day_id=?',
                       ((parse_time(anchor) - timedelta(minutes=180)).isoformat(), day_id))

    def finish_run(self, day_id, account, phase, session_id, status, *, verified_continuation=False):
        if phase != 'review':
            return super().finish_run(day_id, account, phase, session_id, status,
                                      verified_continuation=verified_continuation)
        if status not in ('done', 'U') or verified_continuation:
            raise ValueError('Invalid review outcome')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._wall(db, day_id)
            original = db.execute('SELECT session_id FROM historical_runs WHERE day_id=? AND account_id=?',
                                  (day_id, account)).fetchone()
            if not original or not session_id or session_id != original[0] or self._review_status(db, day_id, account) != 'running':
                raise RuntimeError('Review is not active in this session')
            retry = db.execute("SELECT opened_at FROM online_review_retries WHERE day_id=? AND account_id=? AND status='running'",
                               (day_id, account)).fetchone()
            if retry:
                if status == 'U':
                    self._restore_before_retry(db, day_id, account, retry[0])
                db.execute('UPDATE online_review_retries SET status=? WHERE day_id=? AND account_id=?',
                           (status, day_id, account))
            db.execute('UPDATE online_reviews SET status=? WHERE day_id=? AND account_id=?', (status, day_id, account))
            db.execute('DELETE FROM historical_windows WHERE day_id=? AND account_id=? AND phase=?', (day_id, account, phase))

    def _restore_before_retry(self, db, day_id, account, opened_at):
        # Retain attempted revisions in the audit trail, but restore the active receipt on failure.
        matches = db.execute('SELECT match_id FROM historical_submissions WHERE day_id=? AND account_id=? AND accepted_at>=?',
                             (day_id, account, opened_at)).fetchall()
        for (match_id,) in matches:
            key = day_id, account, match_id
            prior = db.execute('''SELECT payload,sha256,accepted_at,phase FROM online_submission_versions
                                  WHERE day_id=? AND account_id=? AND match_id=? AND accepted_at<?
                                  ORDER BY version DESC LIMIT 1''', (*key, opened_at)).fetchone()
            if prior:
                db.execute('''UPDATE historical_submissions SET payload=?,sha256=?,accepted_at=?,phase=?
                              WHERE day_id=? AND account_id=? AND match_id=?''', (*prior, *key))
            else:
                db.execute('DELETE FROM historical_submissions WHERE day_id=? AND account_id=? AND match_id=?', key)

    def _revisions_allowed(self, phase):
        return phase == 'review'

    def _record_revision(self, db, day_id, account, match_id, body, sha, when, phase):
        version = db.execute('''SELECT COALESCE(MAX(version),0)+1 FROM online_submission_versions
                                WHERE day_id=? AND account_id=? AND match_id=?''', (day_id, account, match_id)).fetchone()[0]
        db.execute('INSERT INTO online_submission_versions VALUES (?,?,?,?,?,?,?,?)',
                   (day_id, account, match_id, version, body, sha, when, phase))

    def advance_clock(self, day_id, value):
        if parse_time(value) > self.now():
            raise ValueError('The online clock cannot advance ahead of wall time')
        return super().advance_clock(day_id, value)
