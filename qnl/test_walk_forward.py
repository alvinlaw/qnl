"""Holdout self-check: selection sees only the train slice, the test slice is
scored once, and planes are cut at the same time fraction.
    python3 -m qnl.test_walk_forward"""
from qnl import signals, stats, walk_forward


def _sr(b, spec):
    return stats.sharpe_period(signals.build(spec[0], spec[1], 0.0)(b))


def main():
    b = {"price": list(range(10)), "flow": {"oi": list(range(10)), "taker": None}}
    tr = walk_forward._slice(b, 0.0, 0.5)
    te = walk_forward._slice(b, 0.5, None)
    assert tr["price"] == list(range(5)) and te["price"] == list(range(5, 10))
    assert tr["flow"]["oi"] == list(range(5)) and te["flow"]["taker"] is None

    # up then down: the train winner is selected on the rise, then scored once
    # on the fall -- no re-selection after seeing the test window
    price = [100.0 + i for i in range(300)] + [400.0 - i for i in range(100)]
    bundle = {"price": price, "funding": [], "basis": None, "flow": None, "opt": None}
    specs = [("momentum", (5,)), ("momentum", (60,))]
    spec, isr, osr = walk_forward.holdout(bundle, specs, frac=0.75, fee=0.0)

    cut = int(len(price) * 0.75)
    train = {"price": price[:cut], "funding": [], "basis": None, "flow": None, "opt": None}
    test = {"price": price[cut:], "funding": [], "basis": None, "flow": None, "opt": None}
    best = max(specs, key=lambda sp: _sr(train, sp))
    assert spec == best, (spec, best)
    assert abs(isr - _sr(train, best)) < 1e-12
    assert abs(osr - _sr(test, best)) < 1e-12
    assert osr < isr  # the fall is out of sample and must not leak into selection

    print("walk-forward self-check OK")


if __name__ == "__main__":
    main()
