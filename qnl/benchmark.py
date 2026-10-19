"""Stage 1: buy-and-hold benchmark — the bar every strategy must clear.

PIT: long from the first close known at `asof`, valued at the last.
    python3 -m qnl.benchmark --symbol BTCUSDT --interval 1d
"""
import argparse
import datetime as dt
import os

from qnl import metrics, store

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")


def buy_and_hold(closes, fee=0.0):
    """Equal-capital long from the first close; equity starts at 1.0.

    fee is charged once at entry (the only trade a buy-and-hold makes).
    """
    base = closes[0]
    return [c / base * (1 - fee) for c in closes]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--asof", default=None, help="UTC date YYYY-MM-DD (default: now)")
    p.add_argument("--fee", type=float, default=0.001, help="per-side taker fee (default 10bps)")
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    con = store.connect(a.db)
    rows = store.load_asof(con, a.symbol, a.interval, store.asof_ms(a.asof))
    if not rows:
        raise SystemExit("no bars asof that date")

    equity = buy_and_hold([r[5] for r in rows], fee=a.fee)  # r[5] = close
    r = metrics.report(equity)
    through = dt.datetime.fromtimestamp(rows[-1][1] / 1000, dt.timezone.utc).date()

    print(f"buy-and-hold {a.symbol} {a.interval} through {through}")
    for k, v in r.items():
        print(f"  {k:>13}: {v:,.4f}" if isinstance(v, float) else f"  {k:>13}: {v}")


if __name__ == "__main__":
    main()
