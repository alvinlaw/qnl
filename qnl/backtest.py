"""Backtest engine: walk a strategy bar by bar.

A strategy is `decide(closes_so_far) -> weight` (see strategies.py). The engine
calls it once per bar with ONLY the bars closed at that point, so it cannot peek
at the future by construction. The weight chosen at bar i's close earns bar i+1's
return.

    python3 -m qnl.backtest --symbol BTCUSDT --family sma_cross --params 20,50
"""
import argparse
import datetime as dt
import os

from qnl import benchmark, metrics, stats, store, strategies, trials

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")


class _Prefix:
    """A lazy view of xs[:n] with no per-bar copy, so the walk is O(n*window).

    Supports exactly the indexing strategies.py uses: len(), c[-1], c[k], c[-w:].
    """

    __slots__ = ("_xs", "_n")

    def __init__(self, xs, n):
        self._xs = xs
        self._n = n

    def __len__(self):
        return self._n

    def __getitem__(self, k):
        if isinstance(k, slice):
            start, stop, step = k.indices(self._n)
            return self._xs[start:stop:step]
        if k < 0:
            k += self._n
        if not 0 <= k < self._n:
            raise IndexError(k)
        return self._xs[k]


def run(closes, decide, fee=0.0):
    """Walk bar by bar, oldest first. Returns the equity curve (starts 1.0).

    fee is per-side (taker), charged on turnover when the weight changes. The
    engine default is 0.0 (pure primitive); entry points pass a realistic fee.
    """
    equity = [1.0]
    prev_w = 0.0
    for i in range(len(closes) - 1):
        w = decide(_Prefix(closes, i + 1))   # info known at close of bar i
        r = closes[i + 1] / closes[i] - 1    # return of bar i+1
        cost = abs(w - prev_w) * fee
        equity.append(equity[-1] * (1 + w * r - cost))
        prev_w = w
    return equity


def parse_params(s):
    """'20,50' -> (20, 50); '20,1.5' -> (20, 1.5)."""
    out = []
    for tok in s.split(","):
        tok = tok.strip()
        if tok:
            out.append(int(tok) if tok.lstrip("-").isdigit() else float(tok))
    return tuple(out)


def _print(name, r):
    print(name)
    for k, v in r.items():
        print(f"  {k:>13}: {v:,.4f}" if isinstance(v, float) else f"  {k:>13}: {v}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--family", default="sma_cross")
    p.add_argument("--params", default="20,50")
    p.add_argument("--asof", default=None, help="UTC date YYYY-MM-DD (default: now)")
    p.add_argument("--fee", type=float, default=0.001, help="per-side taker fee (default 10bps)")
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    rows = store.load_asof(con, a.symbol, a.interval, asof)
    if not rows:
        raise SystemExit("no bars asof that date")
    closes = [r[5] for r in rows]
    through = dt.datetime.fromtimestamp(rows[-1][1] / 1000, dt.timezone.utc).date()

    spec = (a.family, parse_params(a.params))
    eq = run(closes, strategies.build(spec), fee=a.fee)
    _print(f"{strategies.fmt(spec)} {a.symbol} {a.interval} through {through}",
           metrics.report(eq))
    _print(f"buy-and-hold {a.symbol} {a.interval} through {through}",
           metrics.report(benchmark.buy_and_hold(closes, fee=a.fee)))

    trials.record(con, strategies.fmt(spec), a.symbol,
                  stats.sharpe_period(metrics.equity_returns(eq)), len(eq), asof,
                  fee=a.fee)


if __name__ == "__main__":
    main()
