"""Create a migrated copy of a legacy ledger; never modify the input."""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def migrate(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file() or source.is_symlink():
        raise ValueError('Source must be a regular ledger file')
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(f'file:{source.resolve().as_posix()}?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination)) as out:
                src.backup(out)
                if out.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Source integrity check failed')
                with out:
                    out.execute('CREATE TABLE roster (account_id TEXT PRIMARY KEY, active INTEGER NOT NULL, joined_at TEXT NOT NULL, initial_chips REAL NOT NULL)')
                    out.execute('CREATE TABLE roster_audit (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, action TEXT NOT NULL, effective_at TEXT NOT NULL)')
                    out.execute("INSERT INTO roster SELECT account_id, 1, 'unknown', 1000 FROM accounts")
                    out.execute("INSERT INTO roster_audit(account_id,action,effective_at) SELECT account_id, 'legacy-import', 'unknown' FROM accounts")
                if out.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Migrated integrity check failed')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(f'[COMPLETED] Migrated copy: {migrate(args.source, args.destination)}')


if __name__ == '__main__':
    main()
