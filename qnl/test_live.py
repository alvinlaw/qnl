"""Live-book self-check: freeze is immutable, forward is the post-freeze tail.
    python3 -m qnl.test_live"""
import math

from qnl import live, stats, store

_STEP = 28_800_000  # 8h


def main():
    con = store.connect(":memory:")
    store.upsert_klines(con, [
        ("X", "8h", i * _STEP, (i + 1) * _STEP, 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1.0)
        for i in range(600)])

    # a signal book frozen early; a later mark sees the same line plus new bars
    spec = {"name": "sma_cross", "params": [5, 20], "symbol": "X"}
    n0 = live.freeze(con, "sig:X", "signal", spec, 0.001, 300 * _STEP + 1)
    assert n0 > 0
    assert live.freeze(con, "sig:X", "signal", spec, 0.001, 599 * _STEP + 1) == n0  # immutable

    r = [x for x in live.mark(con, 599 * _STEP + 1) if x["book"] == "sig:X"][0]
    assert r["frozen_n"] == n0 < r["total_n"]
    assert r["forward_n"] == r["total_n"] - r["frozen_n"]
    assert math.isfinite(r["live_sr"])

    # forward is exactly the post-freeze slice of the causal series (no re-fit)
    full = live._series(con, "signal", spec, 0.001, 599 * _STEP + 1)
    assert r["live_sr"] == stats.sharpe_period(full[n0:])

    # an unknown kind is a clean error, not a silent empty book
    try:
        live.freeze(con, "bad", "nope", {}, 0.0, 1)
        raise AssertionError("should have rejected an unknown book kind")
    except ValueError:
        pass

    print("live self-check OK")


if __name__ == "__main__":
    main()
