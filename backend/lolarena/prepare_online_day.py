"""Prepare a real online match day from an operator-reviewed manifest."""
import argparse
import json
from pathlib import Path
from .live_online_controller import LiveOnlineController
from .historical_controller import HistoricalController


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--attestation', type=Path, required=True)
    parser.add_argument('--track', choices=('online', 'offline'), default='online')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if set(manifest) != {'day_id', 'anchor', 'anchor_known_at', 'fixtures'}:
        raise ValueError('Invalid manifest fields')
    if not args.apply:
        print('[INFO] Dry run: day was not prepared')
        return
    controller = HistoricalController(args.root) if args.track == 'offline' else LiveOnlineController(args.root)
    controller.prepare_day(manifest['day_id'], manifest['anchor'], manifest['anchor_known_at'],
                           manifest['fixtures'], args.materials, json.loads(args.attestation.read_text()))
    print('[COMPLETED] Match day prepared')


if __name__ == '__main__':
    main()
