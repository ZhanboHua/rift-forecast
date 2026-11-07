"""Run configured online prediction adapters against a prepared Bank ledger."""
import argparse
import concurrent.futures
import fcntl
import json
import hashlib
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import time
from datetime import timedelta
from pathlib import Path

from .live_online_controller import LiveOnlineController
from .gateway_proxy import Gateway, valid_host
from .history_accounts import create_private_file
from . import native_journal
from .native_codex import invoke as invoke_codex
from .historical_controller import digest, encode, parse_time


def run_one(controller, day, account, spec, workspace, materials, resume=False):
    """Run one trusted adapter and submit its JSON predictions to the Bank."""
    if spec.get('protocol') in ('codex', 'offline_codex'):
        return run_codex(controller, day, account, spec, workspace, materials, resume=resume)
    if resume:
        return {'account': account, 'status': 'U', 'reason': 'adapter_has_no_native_resume'}
    session = secrets.token_hex(16)
    controller.open_first(day, account, spec['timeout_seconds'])
    outcome = 'U'
    try:
        token = (controller.root / 'credentials' / (account + '.token')).read_text().strip()
        matches = controller.match_view(day, account)
        account_info = controller.account_view(day, account)
        request = {'account_id': account, 'track': 'online', 'matches': matches['matches'],
                   'deadline': matches['deadline'], 'balance': account_info['balance'],
                   'available': account_info['available'], 'cap': account_info['cap'],
                   'loan_eligible': account_info['loan_eligible'], 'materials': materials}
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8', 'HOME': str(workspace)}
        env.update(spec.get('env_values', {}))
        for target, source in spec.get('env_map', {}).items():
            if source not in os.environ:
                raise ValueError('Missing configured adapter environment variable')
            env[target] = os.environ[source]
        process = subprocess.Popen(spec['command'], cwd=workspace, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            output, _ = process.communicate(json.dumps(request).encode(), timeout=spec['timeout_seconds'])
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            return {'account': account, 'status': 'U', 'reason': 'adapter_timeout'}
        if process.returncode != 0:
            return {'account': account, 'status': 'U', 'reason': 'adapter_failed'}
        if any(os.environ[source].encode() in output for source in spec.get('env_map', {}).values() if os.environ.get(source)):
            raise ValueError('Adapter output contains a configured secret')
        predictions = json.loads(output)
        if not isinstance(predictions, list) or len(predictions) != len(matches['matches']):
            raise ValueError('Adapter must return one prediction per match')
        ids = {match['match_id'] for match in matches['matches']}
        if {prediction.get('match_id') for prediction in predictions if isinstance(prediction, dict)} != ids:
            raise ValueError('Adapter predictions do not match the daily fixtures')
        receipt = controller.submit_many(day, token, predictions)
        outcome = 'done'
        return {'account': account, 'status': 'done', 'receipt': receipt}
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, json.JSONDecodeError):
        return {'account': account, 'status': 'U', 'reason': 'adapter_or_submission_error'}
    finally:
        controller.finish_run(day, account, 'first', session, outcome)


def run_codex(controller, day, account, spec, workspace, materials, resume=False):
    """Serialize recovery for one workspace and run its native CLI."""
    lock_path = workspace / '.native.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_codex_with_gateway(controller, day, account, spec, workspace, materials, resume)
    finally:
        os.close(fd)


def _run_codex_with_gateway(controller, day, account, spec, workspace, materials, resume):
    """Run an online CLI or a network-isolated CLI with its own gateway socket."""
    if spec.get('protocol') != 'offline_codex':
        return _run_codex(controller, day, account, spec, workspace, materials, resume)
    parsed = urlsplit(spec['gateway_url'])
    control_dir = Path(spec.get('control_dir', workspace.parent / '.sandbox-control'))
    control_dir.mkdir(mode=0o700, exist_ok=True)
    if (control_dir.is_symlink() or not control_dir.is_dir() or control_dir.stat().st_mode & 0o077
            or control_dir.stat().st_uid != os.getuid() or control_dir.resolve().is_relative_to(workspace.resolve())):
        raise ValueError('Sandbox control directory must be private')
    home = workspace / '.codex'
    home.mkdir(mode=0o700, exist_ok=resume)
    if home.is_symlink():
        raise ValueError('Invalid native home')
    config = ('model_provider = "gateway"\n[model_providers.gateway]\nname = "gateway"\n'
              'wire_api = "responses"\nenv_key = "CODEX_API_KEY"\nbase_url = ' + json.dumps(spec['gateway_url']) + '\n')
    config_path = home / 'config.toml'
    if resume:
        if config_path.is_symlink() or config_path.read_text() != config:
            raise ValueError('Native gateway configuration changed')
    else:
        create_private_file(config_path, config)
    with tempfile.TemporaryDirectory(prefix='gateway-', dir=control_dir) as temporary:
        socket_path = Path(temporary) / 'gateway.sock'
        gateway = Gateway(socket_path, parsed.hostname)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            wrapped = dict(spec)
            wrapped['command'] = [sys.executable, str(Path(__file__).with_name('offline_sandbox.py')),
                                  '--image', spec['image'], '--workspace', str(workspace),
                                  '--gateway-socket', str(socket_path), '--control-dir', str(control_dir),
                                  '--', *spec['command']]
            return _run_codex(controller, day, account, wrapped, workspace, materials, resume, original_spec=spec)
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join()


def _run_codex(controller, day, account, spec, workspace, materials, resume=False, original_spec=None):
    """Resume only a native session identified by the CLI itself."""
    identity_spec = original_spec or spec
    phase, session, outcome = 'first', None, 'U'
    if resume:
        phase, session = native_journal.recoverable(controller, day, account, identity_spec, workspace)
        with controller.connect() as db:
            state = db.execute('SELECT first_status,session_id,recovery_status FROM historical_runs WHERE day_id=? AND account_id=?', (day, account)).fetchone()
            attempts = db.execute('SELECT COUNT(*) FROM historical_recovery_attempts WHERE day_id=? AND account_id=?', (day, account)).fetchone()[0]
        if not state or (state[1] and state[1] != session):
            raise RuntimeError('Ledger session conflicts with native journal')
        if phase == 'first' and state[0] == 'running':
            controller.finish_run(day, account, 'first', session, 'U')
        elif phase == 'recovery' and state[2] == 'running':
            controller.finish_run(day, account, 'recovery', session, 'U')
        elif state[0] not in ('U', 'done'):
            raise RuntimeError('Native ledger state is not recoverable')
        if attempts >= spec.get('max_recoveries', 1):
            return {'account': account, 'status': 'U', 'reason': 'recovery_limit'}
        controller.open_recovery(day, account, session, min(spec['timeout_seconds'], 1800), missing_summary=True)
        phase = 'recovery'
    else:
        native_journal.prepare(controller, day, account, identity_spec, workspace, 'first')
        controller.open_first(day, account, spec['timeout_seconds'])
    try:
        token = (controller.root / 'credentials' / (account + '.token')).read_text().strip()
        matches = controller.match_view(day, account)
        info = controller.account_view(day, account)
        task = {'account_id': account, 'track': 'offline' if controller.track == 'retrospective-offline' else 'online', 'matches': matches['matches'],
                'deadline': matches['deadline'], 'balance': info['balance'], 'available': info['available'],
                'cap': info['cap'], 'loan_eligible': info['loan_eligible'], 'materials': materials}
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
               'HOME': str(workspace), 'CODEX_HOME': str(workspace / '.codex')}
        env.update(spec.get('env_values', {}))
        for target, source in spec.get('env_map', {}).items():
            if source not in os.environ:
                raise ValueError('Missing configured adapter environment variable')
            env[target] = os.environ[source]
        remaining = spec.get('max_recoveries', 1) + 1
        if resume:
            remaining = spec.get('max_recoveries', 1) - attempts
        for attempt in range(remaining):
            outcome = 'U'
            if phase != 'first' or resume:
                native_journal.prepare(controller, day, account, identity_spec, workspace, phase)
            if identity_spec.get('protocol') == 'offline_codex':
                control = Path(identity_spec.get('control_dir', workspace.parent / '.sandbox-control'))
                receipt = control / ('cleanup-' + hashlib.sha256(str(workspace.resolve()).encode()).hexdigest())
                receipt.unlink(missing_ok=True)
            candidate = invoke_codex(spec['command'], spec['model'], task, env, workspace,
                                     spec['timeout_seconds'], session,
                                     on_start=lambda pid: native_journal.started(controller, day, account, pid),
                                     on_session=lambda value: native_journal.observed(controller, day, account, value),
                                     on_end=lambda pid, code: native_journal.exited(controller, day, account, pid)
                                     if identity_spec.get('protocol') != 'offline_codex' or (code is not None and 0 <= code != 125) else None)
            observed = candidate.get('session_id')
            if session is None and observed:
                session = observed
            if candidate['status'] == 'done':
                predictions = candidate['predictions']
                serialized = json.dumps(predictions, ensure_ascii=False)
                if any(os.environ[source] in serialized for source in spec.get('env_map', {}).values() if os.environ.get(source)):
                    raise ValueError('Adapter output contains a configured secret')
                expected = {match['match_id'] for match in matches['matches']}
                if (len(predictions) != len(expected) or
                        {p.get('match_id') for p in predictions if isinstance(p, dict)} != expected):
                    raise ValueError('Predictions do not match the daily fixtures')
                receipt = controller.submit_many(day, token, predictions)
                outcome = 'done'
            controller.finish_run(day, account, phase, session, outcome,
                                  verified_continuation=phase == 'recovery' and outcome == 'done')
            if outcome == 'done':
                return {'account': account, 'status': 'done', 'receipt': receipt,
                        'session_id': session, 'recoveries': attempt}
            if (candidate.get('reason') in ('session_mismatch', 'event_capture_failed') or not session or attempt + 1 >= remaining
                    or (identity_spec.get('protocol') == 'offline_codex' and (candidate.get('exit_code', 0) < 0 or candidate.get('exit_code') == 125))):
                return {'account': account, 'status': 'U', 'reason': candidate.get('reason', 'native_failed')}
            controller.open_recovery(day, account, session, min(spec['timeout_seconds'], 1800))
            phase = 'recovery'
        return {'account': account, 'status': 'U', 'reason': 'native_failed'}
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        try:
            controller.finish_run(day, account, phase, session, 'U')
        except (ValueError, RuntimeError):
            pass
        return {'account': account, 'status': 'U', 'reason': 'native_or_submission_error'}


def run_day(controller, day, config, workspace_root, material_root, attestation, resume=False):
    if controller.track not in ('online', 'retrospective-offline'):
        raise ValueError('Unsupported track')
    if not isinstance(config, dict) or not config or len(config) > 16:
        raise ValueError('Provide one adapter per account')
    with controller.connect() as db:
        expected = set(controller._day_accounts(db, day))
        row = db.execute('SELECT cutoff,material_sha256 FROM historical_days WHERE day_id=?', (day,)).fetchone()
        fixtures = [json.loads(r[0]) for r in db.execute('SELECT fixture FROM historical_matches WHERE day_id=? ORDER BY rowid', (day,))]
    if not row or row[1] != digest(encode(attestation)):
        raise ValueError('Attestation does not match the prepared day')
    controller.validate_material(material_root, attestation, parse_time(row[0]), fixtures)
    materials = {}
    root = Path(material_root).resolve(strict=True)
    for record in attestation['files']:
        path = (root / record['path']).resolve(strict=True)
        content = path.read_bytes()
        if not path.is_relative_to(root) or len(content) > 2_000_000 or hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError('Material failed validation')
        materials[record['path']] = content.decode('utf-8')
    native_journal.ensure(controller)
    if set(config) != expected:
        raise ValueError('Adapter accounts must match the frozen match-day roster')
    for account, spec in config.items():
        command = spec.get('command') if isinstance(spec, dict) else None
        timeout = spec.get('timeout_seconds') if isinstance(spec, dict) else None
        if (not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command)
                or type(timeout) is not int or not 1 <= timeout <= 3600
                or not isinstance(spec.get('env_values', {}), dict) or not isinstance(spec.get('env_map', {}), dict)
                or any(not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str)
                       for k, v in spec.get('env_values', {}).items())
                or any(any(part in k.upper() for part in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))
                       for k in spec.get('env_values', {}))
                or any(not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str) or not v.isidentifier()
                       for k, v in spec.get('env_map', {}).items())
                or set(spec.get('env_values', {})) & set(spec.get('env_map', {}))
                or {'HOME', 'PATH', 'CODEX_HOME'} & (set(spec.get('env_values', {})) | set(spec.get('env_map', {})))
                or spec.get('protocol', 'json') not in ('json', 'codex', 'offline_codex')
                or (controller.track == 'retrospective-offline' and spec.get('protocol') != 'offline_codex')
                or (controller.track == 'online' and spec.get('protocol') == 'offline_codex')
                or (spec.get('protocol') in ('codex', 'offline_codex') and (not isinstance(spec.get('model'), str) or not spec['model']
                    or type(spec.get('max_recoveries', 1)) is not int or not 0 <= spec.get('max_recoveries', 1) <= 2
                    or (spec.get('max_recoveries', 1) > 0 and timeout > 1800)))):
            raise ValueError('Invalid adapter command, timeout, or environment list')
        if spec.get('protocol') == 'offline_codex':
            url = urlsplit(spec.get('gateway_url', ''))
            if (command != ['codex'] or url.scheme != 'https' or not url.hostname or not valid_host(url.hostname)
                    or url.port not in (None, 443) or url.username or url.password or url.query or url.fragment
                    or not isinstance(spec.get('image'), str) or not spec['image'] or spec['image'].startswith('-')
                    or set(spec.get('env_map', {})) != {'CODEX_API_KEY'} or spec.get('env_values', {})):
                raise ValueError('Offline Codex requires a trusted image, HTTPS gateway, and one API key')
    workspace_root = Path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspaces = {}
    for account in sorted(expected):
        path = workspace_root / account
        if resume:
            if not path.exists() and not path.is_symlink():
                with controller.connect() as db:
                    state = db.execute('SELECT first_status FROM historical_runs WHERE day_id=? AND account_id=?', (day, account)).fetchone()
                if not state or state[0] != 'pending':
                    raise ValueError('Existing private workspace required for recovery')
                path.mkdir(mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise ValueError('Existing private workspace required for recovery')
        else:
            path.mkdir(mode=0o700, exist_ok=False)
        workspaces[account] = path
    def execute(account):
        if resume:
            with controller.connect() as db:
                state = db.execute('SELECT first_status,recovery_status FROM historical_runs WHERE day_id=? AND account_id=?', (day, account)).fetchone()
            if not state:
                return {'account': account, 'status': 'U', 'reason': 'missing_run_state'}
            if state[0] == 'done' and state[1] in ('pending', 'done'):
                return {'account': account, 'status': 'done', 'reason': 'already_complete'}
            retry = state[0] != 'pending'
        else:
            retry = False
        try:
            return run_one(controller, day, account, config[account], workspaces[account], materials, resume=retry)
        except (OSError, ValueError, RuntimeError):
            return {'account': account, 'status': 'U', 'reason': 'recovery_preflight_failed'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(expected)) as pool:
        return list(pool.map(execute, sorted(expected)))



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--day', required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--workspaces', type=Path, required=True)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--attestation', type=Path, required=True)
    parser.add_argument('--track', choices=('online', 'offline'), default='online')
    parser.add_argument('--run', action='store_true', help='Execute adapters; otherwise print a dry-run message')
    parser.add_argument('--resume-interrupted', action='store_true', help='Recover verified interrupted native sessions')
    parser.add_argument('--wait', action='store_true', help='Wait until the first prediction window')
    parser.add_argument('--seal', action='store_true', help='Wait until the lock time and seal the day')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if not args.run:
        print('[INFO] Dry run: no adapter executed; ledger and adapter configuration were not validated')
        return
    if args.track == 'offline':
        from .historical_controller import HistoricalController
        controller = HistoricalController(args.root)
    else:
        controller = LiveOnlineController(args.root)
    attestation = json.loads(args.attestation.read_text())
    if args.wait:
        with controller.connect() as db:
            anchor = parse_time(controller._day(db, args.day)[0])
        while controller.now() < anchor - timedelta(minutes=controller.first_offset):
            time.sleep(min(30, max(0.1, (anchor - timedelta(minutes=controller.first_offset) - controller.now()).total_seconds())))
    results = run_day(controller, args.day, config, args.workspaces, args.materials, attestation, resume=args.resume_interrupted)
    print(json.dumps(results, ensure_ascii=False), flush=True)
    if args.seal:
        with controller.connect() as db:
            anchor = parse_time(controller._day(db, args.day)[0])
        while controller.now() < anchor - timedelta(minutes=10):
            time.sleep(min(30, max(0.1, (anchor - timedelta(minutes=10) - controller.now()).total_seconds())))
        print(json.dumps(controller.seal_day(args.day)), flush=True)


if __name__ == '__main__':
    main()
