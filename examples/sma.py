"""Offline quickstart: synthetic bars -> SMA cross -> backtest + metrics.

    python3 examples/sma.py

No network and no database: the point is the causal primitive -- signals see
only bars up to the current one, and the fee is charged on every turnover.
"""
import math

from qnl import backtest, metrics, stats, strategies


def main():
    closes = [100.0 * math.exp(0.25 * math.sin(i / 30.0)) for i in range(800)]
    decide = strategies.build(("sma_cross", (10, 50)))
    equity = backtest.run(closes, decide, fee=0.001)
    rets = metrics.equity_returns(equity)
    print(f"bars      {len(closes)}")
    print(f"total     {equity[-1] - 1:+.2%}")
    print(f"max DD    {metrics.max_drawdown(equity):.2%}")
    print(f"SR/bar    {stats.sharpe_period(rets):+.4f}")


if __name__ == "__main__":
    main()
