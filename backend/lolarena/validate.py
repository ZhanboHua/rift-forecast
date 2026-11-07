"""Strict prediction.json validation. Standalone (copied into every agent workdir as validate.py).
Usage inside a workdir: python3 validate.py  -> prints OK or the list of errors."""
import json, re, sys
from pathlib import Path

SCORES = ["3-0", "3-1", "3-2", "2-3", "1-3", "0-3"]
ALLOWED = {"match_id", "p_team_a_win", "winner_bet", "score_bets", "rationale", "sources", "take_loan"}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def parse_text(text):
    t = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)  # tolerate a single fenced block
    if m:
        t = m.group(1)
    return json.loads(t)


def score_options(best_of):
    if type(best_of) is not int or best_of not in (1, 3, 5):
        raise ValueError("best_of must be 1, 3, or 5")
    wins = best_of // 2 + 1
    return [f"{wins}-{loser}" for loser in range(wins)] + [f"{loser}-{wins}" for loser in range(wins - 1, -1, -1)]


def validate(obj, match_id, team_a, team_b, best_of=5):
    """Returns (clean, errors). clean has team normalized to the team code."""
    errs = []
    scores = score_options(best_of)
    if not isinstance(obj, dict):
        return None, ["top level must be a JSON object"]
    extra = set(obj) - ALLOWED
    if extra:
        errs.append(f"unknown keys: {sorted(extra)}")
    if str(obj.get("match_id", "")).upper() != match_id.upper():
        errs.append(f"match_id must be {match_id!r}")
    p = obj.get("p_team_a_win")
    if not _num(p) or not 0 <= p <= 1:
        errs.append("p_team_a_win must be a number in [0, 1]")
    wb = obj.get("winner_bet")
    team = None
    if not isinstance(wb, dict):
        errs.append("winner_bet must be an object {team, stake}")
    else:
        t = str(wb.get("team", "")).strip().upper()
        team = {team_a.upper(): team_a, team_b.upper(): team_b, "A": team_a, "B": team_b}.get(t)
        if team is None:
            errs.append(f"winner_bet.team must be {team_a!r} or {team_b!r}")
        if not _num(wb.get("stake")) or wb.get("stake") < 0:
            errs.append("winner_bet.stake must be a number >= 0")
    sb = obj.get("score_bets")
    if not isinstance(sb, dict):
        errs.append("score_bets must be an object with keys " + ", ".join(scores))
    else:
        bad = set(sb) - set(scores)
        if bad:
            errs.append(f"score_bets has invalid keys {sorted(bad)}; allowed {scores} (team_a games first)")
        miss = [k for k in scores if k not in sb]
        if miss:
            errs.append(f"score_bets missing keys {miss}")
        for k, v in sb.items():
            if not _num(v) or v < 0:
                errs.append(f"score_bets[{k}] must be a number >= 0")
    r = obj.get("rationale")
    if not isinstance(r, str) or not r.strip():
        errs.append("rationale must be a non-empty string")
    src = obj.get("sources", [])
    if not isinstance(src, list) or not all(isinstance(s, str) for s in src):
        errs.append("sources must be a list of URL strings")
    tl = obj.get("take_loan", False)
    if not isinstance(tl, bool):
        errs.append("take_loan must be true or false")
    if errs:
        return None, errs
    return {"take_loan": tl, "match_id": match_id, "p_team_a_win": float(p), "winner_bet": {"team": team, "stake": float(wb["stake"])},
            "score_bets": {k: float(sb[k]) for k in scores}, "rationale": r.strip()[:4000], "sources": src}, []


if __name__ == "__main__":
    meta = json.loads(Path("match.json").read_text())
    try:
        obj = parse_text(Path("prediction.json").read_text())
    except FileNotFoundError:
        print("ERROR: prediction.json not found"); sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}"); sys.exit(1)
    clean, errs = validate(obj, meta["match_id"], meta["team_a"], meta["team_b"], meta.get("best_of", 5))
    if errs:
        print("ERRORS:\n- " + "\n- ".join(errs)); sys.exit(1)
    total = clean["winner_bet"]["stake"] + sum(clean["score_bets"].values())
    cap = meta["cap_with_loan"] if clean["take_loan"] and meta.get("loan_eligible") else meta["cap"]
    note = ""
    if clean["take_loan"] and not meta.get("loan_eligible"):
        note += " (take_loan ignored: you are not eligible)"
    if total > cap:
        note += f" (total stake {total:g} > cap {cap:g}: will be scaled down proportionally)"
    print("OK" + note)
