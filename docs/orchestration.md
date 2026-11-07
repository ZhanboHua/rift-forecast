# Run multi-model predictions

The orchestrator validates account predictions through the selected Bank ledger and preserves failures as `U`. Online adapters run as the orchestrator operating-system user; separate workspaces are not a security boundary. The retrospective offline track runs Codex in a network-isolated container with a restricted model gateway, as described below. The example online adapter makes a single model API call.

## Prepare a day

From `backend`, provision a separate online ledger as described in [deployment](deployment.md). Use an operator-reviewed UTF-8 materials directory and a manifest:

```json
{"day_id":"example-day","anchor":"2030-01-01T12:00:00+00:00","anchor_known_at":"2030-01-01T05:00:00+00:00","fixtures":[{"match_id":"example-1","team_a":"AAA","team_b":"BBB","best_of":3,"start":"2030-01-01T12:00:00+00:00","announced_at":"2030-01-01T05:00:00+00:00"}]}
```

Create an attestation with `state` set to `VERIFIED_LIVE_SNAPSHOT`, `cutoff` equal to the anchor minus 400 minutes, `fixtures_sha256` computed as SHA-256 of `lolarena.historical_controller.encode(fixtures)`, and a `files` list. Each entry contains `path` relative to the materials directory, its `sha256`, and `visible_at` no later than the cutoff. The operator is responsible for verifying the origin and time; hashes alone do not prove them. Fixtures must be known by the cutoff and share the match day.

```sh
python -m lolarena.prepare_online_day --root "$LEDGER" --manifest "$MANIFEST" --materials "$MATERIALS" --attestation "$ATTESTATION" --apply
```

## Configure adapters

Each frozen match-day account must have one entry in the JSON configuration. `command` is an argv array, not a shell string. Set non-secret values in `env_values`; map secret environment variables from the parent process with `env_map`. The Bank token stays in the orchestrator. The example below uses the repository's `scripts/openai_compatible_adapter.py`; use its absolute path and replace the endpoint and model names with your own:

```json
{
  "model-a": {"command": ["python3", "/path/to/scripts/openai_compatible_adapter.py"], "timeout_seconds": 120, "env_values": {"MODEL_NAME": "model-a", "MODEL_API_URL": "https://provider.example/v1/chat/completions"}, "env_map": {"MODEL_API_KEY": "MODEL_A_KEY"}},
  "model-b": {"command": ["python3", "/path/to/scripts/openai_compatible_adapter.py"], "timeout_seconds": 120, "env_values": {"MODEL_NAME": "model-b", "MODEL_API_URL": "https://provider.example/v1/chat/completions"}, "env_map": {"MODEL_API_KEY": "MODEL_B_KEY"}}
}
```

The adapter receives one JSON object on stdin with account, fixtures, deadline, account limits and the attested text materials. It returns a JSON array on stdout with exactly one prediction for each match. See `backend/prediction.schema.json`; each score map must contain all valid scores. The example expects a JSON-only response from an OpenAI-compatible chat-completions endpoint. Provider compatibility, availability and cost depend on the selected provider. Operator-written adapters may launch a tool-using CLI, but must enforce their own sandbox and return this same JSON protocol.

## Run and seal

From `backend`, execute this explicitly. Without `--run`, the command is a dry run and does not check the ledger or execute adapters. `--wait` waits until the online first window at T-300 minutes. `--seal` waits until T-10 minutes and asks the controller to seal; it does not settle results.

```sh
python -m lolarena.orchestrator --root "$LEDGER" --day "$DAY_ID" --config "$CONFIG" --workspaces "$PRIVATE_EMPTY_WORKSPACES" --materials "$MATERIALS" --attestation "$ATTESTATION" --run --wait --seal
```

All accounts launch in parallel. The controller checks the online time window and validates submissions. Nonzero exits, timeout, malformed output and submission failures are `U`; never reinterpret them as a pass or a deliberate abstention. The operator must inspect and resolve U before settlement. Workspaces must be new and private. Use a service manager to restart the process after interruption as described below. Non-native adapters cannot be automatically resumed. Inspect all `U` outcomes before settlement.

Model credentials are passed only to the configured child environment, not command arguments or generated logs. The configured endpoint receives the match context and materials. Apply your own provider access, data-handling and spending policies. Tests use local mock adapters and a local mock provider; they do not establish real-provider acceptance or model quality.

## Codex CLI session recovery

For a Codex account, set `protocol` to `codex`, `command` to `["codex"]`, `model` to the desired model, `timeout_seconds` to at most 1800 when enabling recovery, and `max_recoveries` to 0, 1 or 2. Map `CODEX_API_KEY` from the operator's environment using `env_map`; do not put the key in the JSON configuration. Each account uses its own workspace and `CODEX_HOME`. The CLI's `thread.started` event supplies the session ID. If the initial turn fails and has a valid ID, the orchestrator opens a recovery window and runs `codex exec resume` with that exact ID. A completed resumed turn must report the same ID and provide a valid prediction array. A missing, changed or unverified session remains `U`. There is no automatic retry when a session ID was not observed. Process interruption recovery requires the same workspace, configuration and explicit restart option described below.

The integration follows the [official non-interactive Codex documentation](https://learn.chatgpt.com/docs/non-interactive-mode). Native CLI, provider and model compatibility must be verified in the operator's environment.

## Retrospective offline Bank and model gateway

Prepare a separate retrospective ledger and pass `--track offline` to `prepare_online_day` and the orchestrator. Retrospective materials require the historical controller's verified as-of attestation and source proofs; the online attestation is not interchangeable. Do not reuse the online ledger, credentials, or workspaces. Operators obtain and review materials and source permissions themselves.

Configure each frozen account with a trusted Linux image containing Python 3 and Codex CLI, and an HTTPS Responses API gateway on port 443. Example for one account (add an entry for every account):

```json
{
  "model-a": {
    "protocol": "offline_codex",
    "command": ["codex"],
    "model": "your-model",
    "image": "your-trusted-codex-image:tag",
    "gateway_url": "https://model-gateway.example/v1",
    "control_dir": "/private/rift-control",
    "timeout_seconds": 600,
    "max_recoveries": 1,
    "env_map": {"CODEX_API_KEY": "MODEL_A_KEY"}
  }
}
```

The control directory must be owned by the operator, private (`0700`), outside the workspaces and on a filesystem supporting Unix sockets. Set the mapped environment variable securely before starting. The orchestrator mounts an account workspace and a dedicated Unix socket into a Docker container with `--network none`, a read-only root and dropped capabilities. A host-side proxy only connects to the configured public DNS hostname on port 443 and requires the TLS server name to match. Configure normal certificate trust in the image. Private IP gateway endpoints are rejected. The CLI validates TLS, not the proxy. Do not mount the Docker socket, ledger, unrelated credentials or materials into the image. Docker daemon administrators can inspect container environment variables, including the model key.

This restricts direct container network destinations, not what the gateway provider does with a request. Provider-side tools, browsing, routing behind a shared endpoint and model behavior require separate provider controls. The container image and host administrator are trusted; this is not a boundary against a compromised host or Docker daemon. Network isolation does not prove the time or provenance of materials.

```sh
python -m lolarena.orchestrator --track offline --root "$OFFLINE_LEDGER" --day "$DAY_ID" --config "$CONFIG" --workspaces "$PRIVATE_WORKSPACES" --materials "$MATERIALS" --attestation "$ATTESTATION" --run --resume-interrupted --wait --seal
```

## Restart and recovery

Use `--resume-interrupted` on the initial launch and every restart. A process manager must restart the same command on failure and start it at boot; this repository does not install or enable a service. Preserve the ledger, account workspaces, configuration, credentials and control directory across restarts. Ensure only one supervisor manages a day. The sample [systemd unit](../deploy/examples/rift-orchestrator.service) uses `Restart=on-failure` and can be adapted and enabled by the operator after validating its paths, account, Docker access and environment. Supply its `RIFT_LEDGER`, `RIFT_DAY`, `RIFT_CONFIG`, `RIFT_WORKSPACES`, `RIFT_MATERIALS`, `RIFT_ATTESTATION` and mapped model-key variables in the private environment file; protect that file from other users. This is an example and is not installed by the repository. Limit repeated restarts and supervise `U` results; a completed process with unresolved `U` is not a successful prediction.

Each native execution records the CLI-reported session, configuration fingerprint, process identity and boot ID. Recovery verifies these and the ledger before stopping the exact prior process and resuming its session, with at most the configured number of recovery attempts. On the same boot, offline recovery also requires a private cleanup receipt bound to the wrapper process identity. A missing or invalid receipt leaves the result as `U`. Missing identity, changed configuration, ambiguous processes, missing sessions, exhausted retries or an invalid continuation remain `U`. Generic JSON adapters have no automatic resume. Never delete the old workspace or start a new session to hide an uncertain result. Preserve the evidence and resolve it manually. Recovery requires Linux `/proc` and pidfd support. An interruption before the identity is recorded can require manual investigation.

Process-kill tests use fictional agents, local fixtures and isolated containers. A real host reboot, real provider compatibility, model quality and production acceptance require separate deployment validation.

## Fully disconnected utility

`python -m lolarena.offline_sandbox --image IMAGE --workspace PRIVATE_DIR --control-dir PRIVATE_CONTROL_DIR -- COMMAND ...` starts a trusted Docker image with `--network none` and no gateway. No remote API is reachable in this mode. The workspace must contain only regular files and directories; symlinks and sockets are rejected. Keep it private and do not let other processes modify it during execution.
