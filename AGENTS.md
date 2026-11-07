# Repository instructions

- Keep comments and user-facing CLI messages concise and in English. Do not use emoji in code or command output.
- Store temporary files under the repository's `tmp/` or a user-specified private directory, not the system temporary directory.
- Preserve online and retrospective-offline ledgers, match-day snapshots, source timestamps, evidence hashes, and unknown (`U`) outcomes. Never treat missing results as successes.
- Agent and model calls may incur cost or use credentials. Do not start live matches, change production data, or send external messages without explicit authorization.
- Do not commit credentials, source snapshots, agent logs, private ledgers, real evidence, or third-party images. Use fictional fixtures in tests.
- Keep the control API on loopback and never log tokens or model credentials. The Bank defaults to loopback.
- Run tests on the designated test host when instructed. Use isolated temporary directories and exact process or container identifiers. Never change SSH or network services.
- Do not claim network isolation, source permission, or production acceptance without evidence. Run targeted tests and document the scope and limitations of each result.
- Resume a native CLI only with the session ID observed from that CLI. Missing or changed session IDs remain `U`; do not infer session continuity from process exit alone.
- Offline Codex uses a dedicated `--network none` container and a host proxy restricted to the configured gateway hostname and TLS server name. Do not claim provider-side browsing is blocked, real-provider acceptance, or host-reboot recovery without separate evidence. Preserve unknown outcomes and exact process identity during recovery.
