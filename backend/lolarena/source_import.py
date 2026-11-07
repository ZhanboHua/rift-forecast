"""Create local result evidence from operator-supplied source bytes and metadata."""
import argparse
import hashlib
import json
from pathlib import Path

from .historical_controller import parse_time


def import_result(raw_path, metadata_path, evidence_dir):
    raw = Path(raw_path).read_bytes()
    meta = json.loads(Path(metadata_path).read_text(encoding='utf-8'))
    required = {'match_id', 'score', 'result_id', 'source', 'published_at', 'observed_at'}
    if set(meta) != required or any(not isinstance(meta[k], str) or not meta[k] for k in required):
        raise ValueError('Metadata must contain the required nonempty fields')
    published, observed = parse_time(meta['published_at']), parse_time(meta['observed_at'])
    if observed < published:
        raise ValueError('Observation predates publication')
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    raw_hash = hashlib.sha256(raw).hexdigest()
    raw_name = f'{raw_hash}.raw'
    raw_target = root / raw_name
    if raw_target.exists() and (raw_target.is_symlink() or raw_target.read_bytes() != raw):
        raise ValueError('Raw content conflict')
    if not raw_target.exists():
        with raw_target.open('xb') as f:
            f.write(raw)
    record = {**meta, 'published_at': published.isoformat(), 'observed_at': observed.isoformat(),
              'status': 'VERIFIED', 'raw_path': raw_name, 'raw_sha256': raw_hash}
    content = json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
    proof_hash = hashlib.sha256(content).hexdigest()
    proof = root / f'{proof_hash}.json'
    if proof.exists() and (proof.is_symlink() or proof.read_bytes() != content):
        raise ValueError('Evidence content conflict')
    if not proof.exists():
        with proof.open('xb') as f:
            f.write(content)
    return {'path': proof.name, 'sha256': proof_hash, 'observed_at': observed.isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw', type=Path)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('evidence_dir', type=Path)
    args = parser.parse_args()
    print(json.dumps(import_result(args.raw, args.metadata, args.evidence_dir)))


if __name__ == '__main__':
    main()
