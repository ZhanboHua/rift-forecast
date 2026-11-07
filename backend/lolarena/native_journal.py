"""Persist native execution identity for conservative restart recovery on Linux."""
import hashlib
import json
import os
import signal
import time
from pathlib import Path

from .historical_controller import digest, encode


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def process_info(pid):
    try:
        value = Path(f'/proc/{pid}/stat').read_text()
        fields = value[value.rfind(')') + 2:].split()
        return {'state': fields[0], 'group': int(fields[2]), 'start': int(fields[19])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def ensure(controller):
    with controller.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS native_execution (
            day_id TEXT NOT NULL, account_id TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
            workspace TEXT NOT NULL, phase TEXT NOT NULL, session_id TEXT,
            pid INTEGER, boot_id TEXT, start_ticks INTEGER, state TEXT NOT NULL,
            PRIMARY KEY(day_id, account_id))''')


def prepare(controller, day, account, spec, workspace, phase):
    fingerprint = digest(encode(spec))
    with controller.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT spec_sha256,workspace FROM native_execution WHERE day_id=? AND account_id=?', (day, account)).fetchone()
        if row and row != (fingerprint, str(workspace.resolve())):
            raise ValueError('Native configuration or workspace changed')
        if row:
            db.execute("UPDATE native_execution SET phase=?,pid=NULL,boot_id=NULL,start_ticks=NULL,state='launching' WHERE day_id=? AND account_id=?", (phase, day, account))
        else:
            db.execute('INSERT INTO native_execution VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,?)',
                       (day, account, fingerprint, str(workspace.resolve()), phase, 'launching'))


def started(controller, day, account, pid):
    info = process_info(pid)
    if not info or info['group'] != pid:
        raise RuntimeError('Native process identity is unavailable')
    with controller.connect() as db:
        db.execute("UPDATE native_execution SET pid=?,boot_id=?,start_ticks=?,state='running' WHERE day_id=? AND account_id=?",
                   (pid, boot_id(), info['start'], day, account))


def observed(controller, day, account, session):
    with controller.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT session_id FROM native_execution WHERE day_id=? AND account_id=?', (day, account)).fetchone()
        if not row or (row[0] and row[0] != session):
            raise ValueError('Native session changed')
        db.execute('UPDATE native_execution SET session_id=? WHERE day_id=? AND account_id=?', (session, day, account))


def exited(controller, day, account, pid):
    with controller.connect() as db:
        db.execute("UPDATE native_execution SET state='exited' WHERE day_id=? AND account_id=? AND pid=?", (day, account, pid))


def recoverable(controller, day, account, spec, workspace):
    with controller.connect() as db:
        row = db.execute('SELECT spec_sha256,workspace,phase,session_id,pid,boot_id,start_ticks,state FROM native_execution WHERE day_id=? AND account_id=?', (day, account)).fetchone()
    if not row or row[0] != digest(encode(spec)) or row[1] != str(workspace.resolve()) or not row[3] or not row[4]:
        raise RuntimeError('Native session or process identity is unavailable')
    _, _, phase, session, pid, original_boot, start, state = row
    if boot_id() == original_boot:
        current = process_info(pid)
        if current and current['state'] != 'Z':
            if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
                raise RuntimeError('PID-safe recovery is unavailable')
            try:
                pidfd = os.pidfd_open(pid)
            except ProcessLookupError:
                pidfd = None
            if pidfd is not None:
                try:
                    current = process_info(pid)
                    if not current or current['start'] != start or current['group'] != pid:
                        raise RuntimeError('Native PID no longer identifies the recorded process')
                    signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                finally:
                    os.close(pidfd)
            for _ in range(100):
                current = process_info(pid)
                if not current or current['state'] == 'Z':
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Recorded native process has not stopped')
        # Refuse recovery if any member of the original process group is still alive.
        for proc in Path('/proc').iterdir():
            if proc.name.isdigit():
                try:
                    member = process_info(int(proc.name))
                except (PermissionError, ValueError):
                    raise RuntimeError('Cannot verify native process group') from None
                if member and member['group'] == pid and member['state'] != 'Z':
                    raise RuntimeError('Native process group is still active')
        if spec.get('protocol') == 'offline_codex':
            control = Path(spec.get('control_dir', workspace.parent / '.sandbox-control'))
            receipt = control / ('cleanup-' + hashlib.sha256(str(workspace.resolve()).encode()).hexdigest())
            try:
                if receipt.is_symlink() or receipt.stat().st_uid != os.getuid() or receipt.stat().st_mode & 0o077:
                    raise ValueError('Invalid receipt permissions')
                proof = json.loads(receipt.read_text())
                if proof != {'pid': pid, 'boot_id': original_boot, 'start_ticks': start}:
                    raise ValueError('Receipt identity does not match')
            except (OSError, ValueError, TypeError):
                raise RuntimeError('Container cleanup is not verified') from None
    return phase, session
