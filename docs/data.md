# Public data

The site publishes `data/arena.json`, a versioned snapshot with an `observedAt` timestamp. Calendar timestamps and per-track settlement information can have different freshness. The online track and retrospective offline replay should be reported separately. The snapshot is a publication view, not an API stability guarantee; consumers should fail visibly on unsupported schema versions and avoid inferring missing results.

The local demo loads `frontend/data/demo-arena.json`, a bundled fictional fixture. The status skill reads selected fields from the current anonymous public snapshot.
