"""Hand-checked settlement math. Run: python3 tests/test_market.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lolarena import market


def approx(a, b):
    assert abs(a - b) < 1e-6, (a, b)


def test_winner_pool():
    # pool 100+50+50 = 200; winners on DK stake 100+50 = 150 -> payouts 133.33 / 66.67; loser -50
    out, info = market.parimutuel({"x": ("DK", 100), "y": ("DK", 50), "z": ("BLG", 50)}, "DK")
    approx(out["x"]["payout"], 200 * 100 / 150); approx(out["y"]["payout"], 200 * 50 / 150)
    approx(out["z"]["pnl"], -50); approx(sum(o["pnl"] for o in out.values()), 0)  # zero-sum
    assert info["pool"] == 200 and not info["refunded"]


def test_refund_when_nobody_right():
    out, info = market.parimutuel({"x": {"3-0": 10}, "y": {"3-1": 20}}, "2-3")
    assert info["refunded"]; approx(out["x"]["pnl"], 0); approx(out["y"]["pnl"], 0)


def test_score_pool_multi_outcome():
    # x splits 10 on 3-1 and 10 on 3-2; y 20 on 3-1; result 3-1 -> pool 40, winning stake 30
    out, _ = market.parimutuel({"x": {"3-1": 10, "3-2": 10}, "y": {"3-1": 20}, "z": {}}, "3-1")
    approx(out["x"]["payout"], 40 * 10 / 30); approx(out["y"]["payout"], 40 * 20 / 30); approx(out["z"]["pnl"], 0)


def test_cap_scaling():
    w, s, scale = market.normalize_bets(240, {"3-1": 120, "3-2": 120}, 300)  # total 480 > 300
    assert w + sum(s.values()) <= 300 + 1e-9 and abs(scale - 300 / 480) < 1e-6
    w, s, scale = market.normalize_bets(-5, {"3-0": 10}, 300)
    assert w == 0 and s["3-0"] == 10 and scale == 1.0


def test_cap():
    # no fraction limit any more: cap = available chips = balance - open stakes
    approx(market.stake_cap(1000), 1000); approx(market.stake_cap(1000, open_stakes=800), 200); approx(market.stake_cap(0), 0)
    approx(market.stake_cap(30, open_stakes=50), 0)


def test_loan():
    t = market.loan_terms()
    assert t["principal"] == 500 and t["repay"] == 600 and t["threshold"] == 50
    assert market.loan_eligible(49.99, 0) and not market.loan_eligible(50, 0)
    assert market.loan_eligible(0, 1) and not market.loan_eligible(50, 1)


def test_scores():
    approx(market.brier(0.7, True), 0.09); approx(market.brier(0.7, False), 0.49)
    approx(market.log_loss(1.0, False), -__import__("math").log(0.01))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
