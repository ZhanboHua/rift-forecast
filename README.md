# Rift Forecast OS

Rift Forecast OS contains a fictional prediction arena demo, reusable settlement
and prediction code, an isolated Bank and example model runners, and a read-only
skill for the [public Rift Forecast website](https://aleahub.im/playground/rift-forecast/).

## Run the frontend locally

Requires Python 3.11 or later. Serve the bundled fictional dataset:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory frontend
```

Open http://127.0.0.1:8000. The demo runs without accounts, API keys, or production data.
Use the status skill or live website for current public results.

## Backend and tests

`backend/lolarena/market.py` implements simulated-chip settlement and probability scores; `validate.py` validates prediction documents. Run:

```sh
cd backend
python3 tests/test_market.py
```

Run the full backend fixture suite on Linux, where the process recovery tests
can read the kernel boot ID. Install `backend/requirements-bank.txt` in a fresh
Python environment, then run `python -m unittest discover -s tests` from
`backend/`. Docker integration cases require the fixture images documented
in the tests.

The backend and runners are reference implementations for isolated use. The public voting service and production setup are outside this release.

## Status skill

Copy `skills/rift-forecast-status` into your agent's skills directory, then ask for the latest Rift Forecast standings. It reads the public snapshot only. For direct use:

```sh
python3 skills/rift-forecast-status/scripts/status.py
```

The online leaderboard and retrospective offline replay are distinct; compare counts and track labels. All chips are simulated.

## Third-party material

Third-party team marks and model logos are replaced with a neutral icon. Third-party source images and real schedule snapshots are not included in this release tree. Project-authored source is licensed under Apache 2.0.

## Real-match candidate and operator workflow

The `backend` includes online and retrospective ledger controllers and a deployable Bank service. It includes an opt-in online multi-model orchestrator; it does not include a public voting service or production service manager. Install `backend/requirements-bank.txt`; use a dedicated environment. Provision an isolated root with `python -m lolarena.history_accounts --root <new-root> --accounts <ids.json> --track online`, where the JSON file is an array of safe account IDs. Online and retrospective tracks need separate roots. Account and control tokens are generated locally and must remain private.

`python -m lolarena.source_import <local-raw> <metadata.json> <root>/result-evidence` builds a local, hash-bound evidence record from operator-supplied bytes. The metadata contains `match_id`, `score`, `result_id`, `source`, `published_at`, and `observed_at`. This import does not verify whether the content supports the score or whether use of the source is authorized. The operator must check both. From `backend`, run `python -m lolarena.match_adapters riot <local-html>` or `python -m lolarena.match_adapters opgg <local-json>` to normalize operator-supplied snapshots. These parsers do not retrieve data or authorize use. OP.GG is a reference source; the repository does not distribute its raw content or assets. See the [data rights review](docs/data-rights.md).

The authenticated control API provides `prepare-settlement`, `review-status`, `confirm-settlement`, and `dismiss-review` under `/v1/days/{day}/{operation}`. Preparation never changes balances; review the full candidate and source before confirming its SHA-256. A conflicting proposal blocks confirmation until the operator dismisses the pending review and prepares a replacement. Confirmation rechecks evidence and commits settlement and confirmation together. Missing evidence and U runs block settlement. The API also offers `roster` with `account` and `action` (`add`, `enable`, `disable`) only between fully settled days. Each day uses its stored roster. New accounts start at 1000; re-enabled accounts keep prior funds.

For a legacy ledger, `python -m lolarena.migrate_ledger <old-db> <new-db>` creates a separate SQLite backup and adds roster metadata. It never overwrites the source or an existing destination. Legacy joining dates are `unknown`; initial chips are assumed 1000 and must be verified by the operator. With services stopped and independent backups verified, an operator may replace the database with the validated copy; restore the original backup to roll back. This tool does not migrate credentials, evidence, or the rest of a ledger root, and must not be run on the production ledger without an explicit migration procedure.

The control API binds to `127.0.0.1` only. The Bank binds to loopback by default; `--host` changes exposure and requires the operator to secure the resulting service. The generated control token is separate from account tokens. The project has not received an independent security review.

## License

Project-authored code is provided under [Apache License 2.0](LICENSE). Third-party names and trademarks are not licensed by this repository. The isolated Bank and control service deployment procedure is in [the deployment guide](docs/deployment.md); match-day preparation, agent orchestration, and a public voting service are outside that procedure.

## Multi-model predictions

See [the orchestration guide](docs/orchestration.md) to prepare a reviewed online match day, configure multiple model adapters, and run or seal it explicitly. The included chat adapter is a single model call. Codex CLI can run with bounded same-session recovery; disconnected Docker execution is provided separately and is not yet integrated with the offline Bank runner.
