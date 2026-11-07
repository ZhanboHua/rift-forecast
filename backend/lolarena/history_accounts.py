"""Provision private, separate identities for the historical offline track."""

import argparse
import hashlib
import json
import os
import secrets
import re
from datetime import datetime, timezone
import sqlite3
from contextlib import closing
from pathlib import Path

HISTORICAL_ACCOUNTS = ('gpt-6.1-sol', 'claude-opus-5-5', 'gemini-4-argon')
FULL_HISTORICAL_ACCOUNTS = HISTORICAL_ACCOUNTS + (
    'doubao-seed-2-1-pro-260915', 'hy4-preview', 'step-5-preview', 'glm-5.3',
    'deepseek-v4.1-flash', 'kimi-k3.1-pro', 'grok-4.6', 'qwen3.8-max-0902',
)
DEEPSEEK_REPLAY_ACCOUNTS = ('deepseek-v4.1-flash',)
HISTORICAL_ROSTERS = (HISTORICAL_ACCOUNTS, FULL_HISTORICAL_ACCOUNTS, DEEPSEEK_REPLAY_ACCOUNTS)
INITIAL_CHIPS = 1000


def create_private_file(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def provision_control_credential(root):
    root = Path(root)
    if not (root / 'bank/accounts.sqlite3').is_file():
        raise ValueError('Historical account ledger is required')
    create_private_file(root / 'control-auth.token', secrets.token_urlsafe(48) + '\n')


def provision(root, accounts=HISTORICAL_ACCOUNTS, *, track='retrospective-offline'):
    if (not accounts or len(accounts) != len(set(accounts)) or
            any(not isinstance(a, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', a) for a in accounts) or
            track not in ('retrospective-offline', 'online')):
        raise ValueError('Unsupported account roster or track')
    root = Path(root)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    tokens = root / 'credentials'
    tokens.mkdir(mode=0o700)
    bank_dir = root / 'bank'
    bank_dir.mkdir(mode=0o700)
    database = bank_dir / 'accounts.sqlite3'
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE accounts (account_id TEXT PRIMARY KEY, token_sha256 TEXT UNIQUE NOT NULL, '
                   'balance REAL NOT NULL, debt REAL NOT NULL, loans_taken INTEGER NOT NULL)')
        db.execute('CREATE TABLE roster (account_id TEXT PRIMARY KEY, active INTEGER NOT NULL, joined_at TEXT NOT NULL, initial_chips REAL NOT NULL)')
        db.execute('CREATE TABLE roster_audit (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, action TEXT NOT NULL, effective_at TEXT NOT NULL)')
        for account in accounts:
            token = secrets.token_urlsafe(48)
            create_private_file(tokens / (account + '.token'), token + '\n')
            db.execute('INSERT INTO accounts VALUES (?,?,?,?,?)',
                       (account, hashlib.sha256(token.encode()).hexdigest(), INITIAL_CHIPS, 0, 0))
            joined = datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO roster VALUES (?,?,?,?)', (account, 1, joined, INITIAL_CHIPS))
            db.execute('INSERT INTO roster_audit(account_id,action,effective_at) VALUES (?,?,?)', (account, 'add', joined))
    database.chmod(0o600)
    if track == 'online':
        create_private_file(bank_dir / 'ONLINE_LEDGER', 'online\n')
    provision_control_credential(root)
    manifest = {'track': track, 'state': 'PROVISIONED_NOT_EXPOSED',
                'account_ids': list(accounts), 'initial_chips': INITIAL_CHIPS,
                'bank_ready': False, 'replay_ready': False}
    create_private_file(root / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--accounts', type=Path, help='JSON array of account IDs')
    parser.add_argument('--track', choices=('retrospective-offline', 'online'), default='retrospective-offline')
    args = parser.parse_args()
    os.umask(0o077)
    accounts = json.loads(args.accounts.read_text()) if args.accounts else HISTORICAL_ACCOUNTS
    if not isinstance(accounts, (list, tuple)):
        parser.error('accounts must be a JSON array')
    result = provision(args.root, tuple(accounts), track=args.track)
    print(json.dumps({'track': result['track'], 'accounts': len(result['account_ids']),
                      'state': result['state'], 'replay_ready': result['replay_ready']}))


if __name__ == '__main__':
    main()
