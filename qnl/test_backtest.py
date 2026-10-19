"""Backtest / strategy self-check.   python3 -m qnl.test_backtest"""
from qnl import backtest, strategies


def main():
    # 1. no-lookahead: decide is only ever handed bars up to i, never the last bar
    seen = []
    backtest.run([100.0, 110.0, 121.0, 133.1], lambda c: (seen.append(len(c)), 1.0)[1])
    assert seen == [1, 2, 3], seen

    # 2. always-long earns buy-and-hold (same curve, up to float rounding)
    closes = [100.0, 110.0, 90.0]
    eq = backtest.run(closes, lambda c: 1.0)
    assert all(abs(x - y) < 1e-12 for x, y in zip(eq, [1.0, 1.1, 0.9])), eq

    # 3. always-flat never moves
    assert backtest.run(closes, lambda c: 0.0) == [1.0, 1.0, 1.0]

    # 4. fees cut a flipping strategy's equity; a flat one pays nothing
    flip = lambda c: float(len(c) % 2)
    assert backtest.run(closes, flip, fee=0.01)[-1] < backtest.run(closes, flip, fee=0.0)[-1]
    assert backtest.run(closes, lambda c: 0.0, fee=0.01) == [1.0, 1.0, 1.0]

    # 5. parse_params mixes ints and floats
    assert backtest.parse_params("20,1.5") == (20, 1.5)

    # 6. strategy families honour their edge cases
    assert strategies.sma_cross(2, 3)([10.0, 10.0]) == 0.0       # not enough history
    assert strategies.sma_cross(2, 3)([1.0, 2.0, 3.0]) == 1.0    # fast > slow
    assert strategies.sma_cross(2, 3)([3.0, 2.0, 1.0]) == 0.0    # fast < slow
    assert strategies.momentum(2)([1.0, 2.0, 3.0]) == 1.0        # up over lookback
    assert strategies.momentum(2)([3.0, 2.0, 1.0]) == 0.0        # down
    assert strategies.mean_revert(3, 1.0)([10.0, 10.0, 1.0]) == 1.0  # deep dip -> long
    assert strategies.mean_revert(3, 1.0)([1.0, 10.0, 10.0]) == 0.0  # spike -> flat
    w = strategies.vol_target(20, 0.5)([100.0 * 1.001 ** i for i in range(25)])
    assert 0.0 <= w <= 1.0

    print("backtest self-check OK")


if __name__ == "__main__":
    main()
