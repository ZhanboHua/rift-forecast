"""Pure market math: stake validation, parimutuel settlement, scoring rules. No I/O."""
import math
from .config import SCORES, arena_cfg


def standings(net_by_account):
    """Rank accounts by net chips with a stable order for ties."""
    ordered = sorted(net_by_account.items(), key=lambda row: (-row[1], row[0]))
    return [{'rank': rank, 'account_id': account, 'net': net}
            for rank, (account, net) in enumerate(ordered, 1)]


def stake_cap(balance, open_stakes=0.0, fraction=None):
    """Max total stake (winner + score markets combined) for one match = available chips (fraction=1.0)."""
    f = arena_cfg()["stake_cap_fraction"] if fraction is None else fraction
    return round(max(0.0, min(f * balance, balance - open_stakes)), 2)


def loan_terms():
    L = arena_cfg()["loan"]
    return {"principal": float(L["principal"]), "repay": round(L["principal"] * (1 + L["interest_rate"]), 2),
            "threshold": round(L["threshold_fraction"] * arena_cfg()["initial_chips"], 2), "max": L["max_per_account"]}


def loan_eligible(available, loans_taken):
    """Check the balance supplied by the track and its optional lifetime loan cap."""
    t = loan_terms()
    return available < t["threshold"] and (t["max"] is None or loans_taken < t["max"])


def normalize_bets(winner_stake, score_stakes, cap, scores=None):
    """Clamp negatives to 0, round to 0.01 chips; if the combined stake exceeds cap,
    scale every stake down by the same factor. Returns (winner_stake, score_stakes, scale)."""
    w = max(0.0, float(winner_stake or 0))
    s = {k: max(0.0, float(score_stakes.get(k, 0) or 0)) for k in (SCORES if scores is None else scores)}
    total = w + sum(s.values())
    scale = 1.0
    if total > cap + 1e-9:
        scale = cap / total if total > 0 else 0.0
        w *= scale
        s = {k: v * scale for k, v in s.items()}
    floor2 = lambda x: math.floor(x * 100) / 100  # floor so rounding never breaks the cap
    return floor2(w), {k: floor2(v) for k, v in s.items()}, round(scale, 6)


def parimutuel(stakes, winning_outcome):
    """stakes: {participant: (outcome, amount)} or {participant: {outcome: amount}}.
    Whole pool goes to stakers on winning_outcome pro-rata; if nobody staked on it,
    everyone is refunded. Returns {participant: {"staked", "payout", "pnl"}} plus pool info."""
    per = {}
    for who, st in stakes.items():
        if isinstance(st, tuple):
            st = {st[0]: st[1]} if st[0] is not None else {}
        per[who] = {o: float(a) for o, a in st.items() if a and a > 0}
    pool = sum(sum(d.values()) for d in per.values())
    win_total = sum(d.get(winning_outcome, 0.0) for d in per.values())
    out = {}
    for who, d in per.items():
        staked = sum(d.values())
        if win_total > 0:
            payout = pool * d.get(winning_outcome, 0.0) / win_total
        else:
            payout = staked  # refund
        out[who] = {"staked": staked, "payout": payout, "pnl": payout - staked}  # unrounded: keeps the pool exactly zero-sum
    return out, {"pool": round(pool, 2), "winning_stake": round(win_total, 2), "refunded": win_total == 0 and pool > 0}


def brier(p_a, a_won):
    return (p_a - (1.0 if a_won else 0.0)) ** 2


def log_loss(p_a, a_won, eps=0.01):
    p = min(max(p_a, eps), 1 - eps)
    return -math.log(p if a_won else 1 - p)
