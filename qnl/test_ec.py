"""Equity-curve overlay self-check.   python3 -m qnl.test_ec"""
from qnl import ec


def main():
    r = [0.1, 0.1, 0.1, -0.5, 0.1, 0.1]
    assert ec.overlay(r) == r                     # no filter -> identity
    got = ec.overlay(r, ma=2)
    assert got == [0.1, 0.1, 0.1, -0.5, 0.0, 0.1], got
    # drawdown gate: flat while the shadow equity is below peak*(1-dd), then
    # re-enters once the shadow recovers (the shadow keeps following the book)
    d = [0.1, -0.05, 0.1, 0.1]
    assert ec.overlay(d, dd=0.02) == [0.1, -0.05, 0.0, 0.1], ec.overlay(d, dd=0.02)
    # PIT: later bars cannot change earlier outputs
    for k in (2, 4, 6):
        assert ec.overlay(r[:k], ma=2) == got[:k], k
    print("ec self-check OK")


if __name__ == "__main__":
    main()
