"""Paths, env, and static config. LOLARENA_HOME overrides the data dir; LOLARENA_MATCHES overrides the match file (used by the smoke test)."""
import json, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = Path(os.environ.get("LOLARENA_HOME", ROOT / "data"))
BJT = timezone(timedelta(hours=8))
SCORES = ["3-0", "3-1", "3-2", "2-3", "1-3", "0-3"]  # always team_a games first
STAKE_CAP_FRACTION = 0.30


def load_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def models_cfg():
    return load_json(os.environ.get("LOLARENA_MODELS") or CONFIG / "models.json")


def enabled_models(only=None):
    ms = [m for m in models_cfg()["models"] if m.get("enabled")]
    if only:
        want = set(only)
        ms = [m for m in models_cfg()["models"] if m["id"] in want]
        missing = want - {m["id"] for m in ms}
        if missing:
            raise SystemExit(f"unknown model(s): {sorted(missing)}")
    return ms


def teams():
    return load_json(os.environ.get("LOLARENA_TEAMS") or CONFIG / "teams.json")


def matches_cfg():
    return load_json(os.environ.get("LOLARENA_MATCHES") or CONFIG / "matches.json")


def parse_time(s):
    return datetime.fromisoformat(s)


def now():
    return datetime.now(BJT)


def arena_cfg():
    cfg = load_json(CONFIG / "arena.json")
    if os.environ.get("LOLARENA_EGRESS_MODE"):
        cfg["egress"]["mode"] = os.environ["LOLARENA_EGRESS_MODE"]
    if os.environ.get("LOLARENA_EGRESS_URL"):
        cfg["egress"]["proxy_url"] = os.environ["LOLARENA_EGRESS_URL"]
    return cfg


def harnesses():
    return load_json(CONFIG / "harnesses.json")["harnesses"]


def tracks():
    return list(arena_cfg()["tracks"].keys())
