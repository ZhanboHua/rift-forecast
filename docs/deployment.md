# Deploy the Bank on one Linux host

This guide covers the Bank and control API only. The opt-in [online orchestrator](orchestration.md) runs on the host or in a separately managed environment. The repository does not install a production service manager or a public voting service.

## Build and provision

Build from a reviewed release checkout with Docker. Run the container as the host user who owns the ledger. Keep the ledger on private, backed-up storage outside the checkout. The `--network host` examples target Linux and preserve each service's loopback binding.

```sh
docker build -t rift-forecast-bank:local .
mkdir -p "$PWD/runtime"
printf '["agent-1"]\n' > "$PWD/runtime/accounts.json"
```

Initialize a new online ledger inside a one-shot container. Do not reuse a directory containing an existing ledger.

```sh
docker run --rm --network none --user "$(id -u):$(id -g)" --cap-drop ALL \
  --security-opt no-new-privileges --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PWD/runtime,dst=/data" rift-forecast-bank:local \
  python -m lolarena.history_accounts --root /data/online --accounts /data/accounts.json --track online
```

The ledger contains participant tokens, an administrator token, SQLite data and evidence. Restrict access to the operating user. Keep online and retrospective ledger roots separate. The following commands require a prepared match day named `DAY_ID`. The preparation workflow and evidence audit must finish before serving it.

```sh
docker run -d --name rift-bank --network host --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PWD/runtime/online,dst=/data" rift-forecast-bank:local \
  python -m lolarena.historical_bank --root /data --day-id DAY_ID --online \
  --allowed-host 127.0.0.1:8080 --port 8080

docker run -d --name rift-control --network host --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PWD/runtime/online,dst=/data" rift-forecast-bank:local \
  python -m lolarena.historical_control_api --root /data --online --port 8765
```

Both endpoints listen on `127.0.0.1` by default. Control operations require the administrator token, which is distinct from participant tokens. Do not expose the control API to a network. A Bank exposed beyond the host needs a separately configured TLS and access boundary; it is outside this guide. Do not log token values. Check `GET /healthz` on the Bank, inspect container logs for errors, and verify an unauthenticated control request is rejected.

## Backup and restore

Quiesce the application by stopping only its two named containers, and ensure no other process can write the ledger. Create a private backup of the *entire* ledger root, including SQLite, credentials, attestations and evidence, preserving permissions. Verify file hashes and SQLite `PRAGMA integrity_check`. Protect the backup as sensitive because it contains live credentials. Test restore to a new, isolated path before relying on it; compare file hashes and verify the restored database and account authentication. Restart only the named application containers after verifying the correct root and access permissions. A database-only copy does not contain the other required files. Never copy a live SQLite database as an ordinary file while writers are active.
