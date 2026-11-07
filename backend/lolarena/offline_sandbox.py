"""Run a trusted image with no Docker network and one private workspace."""
import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import tempfile
import stat
import subprocess
import sys
from pathlib import Path


def command(image, workspace, argv, gateway_socket=None):
    if not image or image.startswith('-') or not argv or not all(isinstance(x, str) and x for x in argv) or argv[0].startswith('-'):
        raise ValueError('Image and command are required')
    path = Path(workspace).resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Workspace must be a directory')
    if ',' in str(path) or '\n' in str(path):
        raise ValueError('Workspace path contains unsupported characters')
    for item in path.rglob('*'):
        mode = item.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('Workspace may contain only regular files and directories')
    args = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128',
            '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=32m',
            '--mount', f'type=bind,src={path},dst=/workspace', '--workdir', '/workspace']
    if gateway_socket is None:
        return [*args, '--entrypoint', argv[0], image, *argv[1:]]
    gateway = Path(gateway_socket)
    if gateway.is_symlink() or not stat.S_ISSOCK(gateway.lstat().st_mode) or gateway.lstat().st_uid != os.getuid():
        raise ValueError('Gateway must be an owned Unix socket')
    gateway = gateway.resolve(strict=True)
    if ',' in str(gateway) or '\n' in str(gateway):
        raise ValueError('Gateway path contains unsupported characters')
    proxy = Path(__file__).with_name('unix_proxy.py').resolve(strict=True)
    return [*args, '--env', 'HOME=/workspace', '--env', 'CODEX_HOME=/workspace/.codex',
            '--env', 'CODEX_API_KEY', '--mount', f'type=bind,src={gateway},dst=/gateway.sock,readonly',
            '--mount', f'type=bind,src={proxy},dst=/runner_proxy.py,readonly',
            '--entrypoint', 'python3', image, '/runner_proxy.py', '--socket', '/gateway.sock', '--', *argv]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--gateway-socket', type=Path)
    parser.add_argument('--control-dir', required=True, type=Path)
    parser.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
    control = args.control_dir.resolve(strict=True)
    workspace = args.workspace.resolve(strict=True)
    if not control.is_dir() or control.is_relative_to(workspace) or control.stat().st_mode & 0o077:
        raise ValueError('Control directory must be private and outside the workspace')
    run_label = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix='sandbox-', dir=control) as temporary:
        cidfile = Path(temporary) / 'cid'
        docker_args = command(args.image, workspace, argv, args.gateway_socket)
        docker_args[3:3] = ['--cidfile', str(cidfile), '--label', 'org.rift-forecast.test-run=' + run_label]
        child = None
        def interrupted(signum, frame):
            raise InterruptedError('Sandbox interrupted')
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            child = subprocess.Popen(docker_args)
            return child.wait()
        finally:
            signal.signal(signal.SIGTERM, previous)
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            if not cidfile.is_file() or cidfile.is_symlink():
                raise RuntimeError('Container identity was not recorded')
            cid = cidfile.read_text().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', cid):
                raise RuntimeError('Invalid container identity')
            listed = subprocess.run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + cid],
                                    capture_output=True, text=True, timeout=10, check=True)
            if listed.stdout.strip():
                inspected = subprocess.run(['docker', 'inspect', '--format',
                                            '{{index .Config.Labels "org.rift-forecast.test-run"}}', cid],
                                           capture_output=True, text=True, timeout=10, check=True)
                if inspected.stdout.strip() != run_label:
                    raise RuntimeError('Container identity label does not match')
                subprocess.run(['docker', 'rm', '-f', cid], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15, check=True)
            stat_data = Path(f'/proc/{os.getpid()}/stat').read_text()
            start = int(stat_data[stat_data.rfind(')') + 2:].split()[19])
            receipt = control / ('cleanup-' + hashlib.sha256(str(workspace).encode()).hexdigest())
            payload = {'pid': os.getpid(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       'start_ticks': start}
            fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print('[WARNING] Sandbox cleanup or execution failed', file=sys.stderr)
        sys.exit(125)
