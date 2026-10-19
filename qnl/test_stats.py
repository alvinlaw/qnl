"""Degenerate-series guard: a numerically constant return series has no Sharpe."""
from qnl import stats


def main():
    const = [0.001] * 49
    assert stats.sharpe_period(const) == 0.0
    assert stats.dsr(const, 0.0, 10, 0.0) == 0.0
    assert stats.sharpe_period([0.0] * 49) == 0.0

    # a real, varying series still scores
    rising = [0.001 * (1 + (i % 3)) for i in range(60)]
    assert stats.sharpe_period(rising) > 0.0
    assert 0.0 < stats.dsr(rising, 0.0, 10, 0.0001) <= 1.0


if __name__ == "__main__":
    main()
    print("ok: stats")
