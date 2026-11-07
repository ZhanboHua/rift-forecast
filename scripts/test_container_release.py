"""Run an isolated container deployment and full fictional ledger restore."""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'backend/tests'))
from test_historical_controller import HistoricalControllerTest


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def port():
    with closing(socket.socket()) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def wait(url, expected):
    for _ in range(100):
        try:
            with urlopen(url, timeout=1) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        except (OSError, URLError):
            time.sleep(.1)
            continue
        if status == expected:
            return
        time.sleep(.1)
    raise RuntimeError('Service did not reach expected status')


def post(url, payload, token):
    request = Request(url, data=json.dumps(payload).encode(), method='POST',
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urlopen(request, timeout=5) as response:
        return json.load(response)['result']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    work = ROOT / 'backend/tmp'
    work.mkdir(exist_ok=True)
    fixture = HistoricalControllerTest(methodName='runTest')
    fixture.setUp()
    containers = []
    try:
        with tempfile.TemporaryDirectory(dir=work) as provisioning_dir:
            base = Path(provisioning_dir)
            (base / 'accounts.json').write_text('["fixture-agent"]')
            run('docker', 'run', '--rm', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp',
                '--mount', f'type=bind,src={base},dst=/data', args.image,
                'python', '-m', 'lolarena.history_accounts', '--root', '/data/online',
                '--accounts', '/data/accounts.json', '--track', 'online')
            assert (base / 'online/bank/ONLINE_LEDGER').is_file()
            assert (base / 'online/credentials/fixture-agent.token').stat().st_mode & 0o077 == 0
            assert (base / 'online/control-auth.token').stat().st_mode & 0o077 == 0
        fixture.clock.value = datetime.now(timezone.utc)
        fixture.prepare()
        for account in fixture.controller.accounts:
            fixture.controller.open_first('day-1', account, 60)
            fixture.controller.finish_run('day-1', account, 'first', 'session', 'done')
            fixture.controller.open_recovery('day-1', account, 'session', 60)
            fixture.controller.finish_run('day-1', account, 'recovery', 'session', 'done')
        fixture.controller.seal_day('day-1')
        published = fixture.anchor + timedelta(hours=2)
        fixture.controller.advance_clock('day-1', published.isoformat())
        evidence = fixture.evidence(published)
        token = (fixture.root / 'control-auth.token').read_text().strip()
        ctrl_port, bank_port = port(), port()
        if ctrl_port == bank_port:
            bank_port = port()
        common = ['docker', 'run', '-d', '--network', 'host', '--user', f'{os.getuid()}:{os.getgid()}',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp',
                  '--mount', f'type=bind,src={fixture.root},dst=/data', args.image, 'python', '-m']
        control = run(*common, 'lolarena.historical_control_api', '--root', '/data', '--port', str(ctrl_port))
        containers.append(control)
        bank = run(*common, 'lolarena.historical_bank', '--root', '/data', '--day-id', 'day-1',
                   '--allowed-host', f'127.0.0.1:{bank_port}', '--port', str(bank_port))
        containers.append(bank)
        bank_url = f'http://127.0.0.1:{bank_port}/healthz'
        base = f'http://127.0.0.1:{ctrl_port}/v1/days/day-1/'
        wait(bank_url, 200)
        wait(base + 'review-status', 405)
        payload = {'match_id': 'fictional-1', 'score': '1-0', 'result_id': 'result-1',
                   'source': 'fixture', 'published_at': published.isoformat(), 'evidence': evidence}
        pending = post(base + 'prepare-settlement', payload, token)
        confirmation = {'match_id': 'fictional-1', 'sha256': pending['sha256']}
        assert not post(base + 'confirm-settlement', confirmation, token)['duplicate']
        run('docker', 'restart', control)
        wait(base + 'review-status', 405)
        assert post(base + 'confirm-settlement', confirmation, token)['duplicate']
        run('docker', 'stop', bank, control)
        with tempfile.TemporaryDirectory(dir=work) as tmp:
            backup, restored = Path(tmp) / 'backup', Path(tmp) / 'restored'
            shutil.copytree(fixture.root, backup, symlinks=True)
            shutil.copytree(backup, restored, symlinks=True)
            assert manifest(fixture.root) == manifest(backup) == manifest(restored)
            restore_common = common.copy()
            mount_index = restore_common.index('--mount') + 1
            restore_common[mount_index] = f'type=bind,src={restored},dst=/data'
            restored_port = port()
            restored_control = run(*restore_common, 'lolarena.historical_control_api', '--root', '/data', '--port', str(restored_port))
            containers.append(restored_control)
            restored_base = f'http://127.0.0.1:{restored_port}/v1/days/day-1/'
            wait(restored_base + 'review-status', 405)
            assert post(restored_base + 'confirm-settlement', confirmation, token)['duplicate']
            run('docker', 'stop', restored_control)
        print('[COMPLETED] Container settlement, restart, backup and restore checks passed')
    finally:
        for container in reversed(containers):
            subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture.tearDown()


if __name__ == '__main__':
    main()
