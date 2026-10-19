"""CLI: pull Binance klines into the PIT store.

    python3 -m qnl.ingest --symbol BTCUSDT --interval 1d --start 2017-01-01
"""
import argparse
import datetime as dt
import os

from qnl import binance, funding, store

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")


def _to_ms(date_str):
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def add_args(p):
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--start", default="2017-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--funding", action="store_true", help="ingest 8h funding instead of klines")
    p.add_argument("--mark", action="store_true",
                   help="ingest perp mark-price klines at --interval, stored as <interval>_mark")
    p.add_argument("--db", default=_DEFAULT_DB)


def run(a):
    """Public ingest paths; plugins handle their own flags before calling this."""
    if a.mark:
        tag = f"{a.interval}_mark"
        raw = funding.fetch_mark(a.symbol, a.interval, _to_ms(a.start),
                                 _to_ms(a.end) if a.end else None)
        rows = binance.to_rows(a.symbol, tag, raw)
        con = store.connect(a.db)
        store.upsert_klines(con, rows)
        n = con.execute("SELECT COUNT(*) FROM klines WHERE symbol=? AND interval=?",
                        (a.symbol, tag)).fetchone()[0]
        print(f"{a.symbol} {tag}: fetched {len(rows)}, store now holds {n} rows -> {a.db}")
        return

    if a.funding:
        raw = funding.fetch_raw(a.symbol, _to_ms(a.start),
                                _to_ms(a.end) if a.end else None)
        rows = funding.to_rows(a.symbol, raw)
        con = store.connect(a.db)
        store.upsert_funding(con, rows)
        n = con.execute("SELECT COUNT(*) FROM funding WHERE symbol=?",
                        (a.symbol,)).fetchone()[0]
        print(f"{a.symbol} funding: fetched {len(rows)}, store now holds {n} rows -> {a.db}")
        return

    raw = binance.fetch_raw(a.symbol, a.interval, _to_ms(a.start),
                            _to_ms(a.end) if a.end else None)
    rows = binance.to_rows(a.symbol, a.interval, raw)

    con = store.connect(a.db)
    store.upsert_klines(con, rows)
    n = con.execute("SELECT COUNT(*) FROM klines WHERE symbol=? AND interval=?",
                    (a.symbol, a.interval)).fetchone()[0]
    print(f"{a.symbol} {a.interval}: fetched {len(rows)}, store now holds {n} rows -> {a.db}")


def main():
    p = argparse.ArgumentParser()
    add_args(p)
    run(p.parse_args())


if __name__ == "__main__":
    main()
