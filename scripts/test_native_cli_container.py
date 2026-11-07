"""Exercise an installed Codex CLI against a local fictional Responses server."""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from lolarena.offline_sandbox import command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Trusted image containing Python and Codex CLI')
    args = parser.parse_args()
    tmp = ROOT / 'backend/tmp'
    tmp.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp) as workspace:
        path = Path(workspace)
        shutil.copy(ROOT / 'backend/lolarena/native_codex.py', path / 'native_codex.py')
        shutil.copy(ROOT / 'backend/tests/fixtures/codex_local_gateway.py', path / 'check_codex.py')
        result = subprocess.run(command(args.image, path, ['python3', '/workspace/check_codex.py']),
                                capture_output=True, text=True, timeout=90, check=True)
        print(result.stdout, end='')


if __name__ == '__main__':
    main()
