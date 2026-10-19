"""Benchmark / metrics self-check.   python3 -m qnl.test_benchmark"""
from qnl import benchmark, metrics


def main():
    closes = [100.0, 110.0, 90.0, 99.0]
    eq = benchmark.buy_and_hold(closes)
    assert eq[0] == 1.0 and eq[1] == 1.1
    r = metrics.report(eq, periods_per_year=4)
    assert abs(r["total_return"] - (-0.01)) < 1e-12
    assert abs(r["max_drawdown"] - (0.9 / 1.1 - 1)) < 1e-12

    flat = metrics.report([1.0, 1.0, 1.0], periods_per_year=4)
    assert flat["total_return"] == 0.0
    assert flat["max_drawdown"] == 0.0
    assert flat["sharpe"] == 0.0
    print("benchmark self-check OK")


if __name__ == "__main__":
    main()
