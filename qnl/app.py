"""qnl.app: one autonomous cycle -- ingest fresh data, run the AI-native loop,
write a timestamped report. The driver that makes the spine runnable end-to-end
without hand-chaining commands.

    python3 -m qnl.app --symbols BTCUSDT,ETHUSDT --backend offline
    python3 -m qnl.app --backend deepseek --rounds 8    # LLM proposer (paid)
    python3 -m qnl.app --no-ingest                      # loop on stored data only

shortcut: sync() ingests spot 8h + mark + funding only (the planes every family
needs); flow/options are separate rate-limited ingests. Add them when a family
that needs them is being swept.
"""
import argparse
import datetime as dt
import json
import os
import time

from qnl import (benchmark, binance, funding, generator, live, loop, metrics,
                 signals, sources, store, trials)

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
_DEFAULT_START = "2020-01-01"
_INTERVAL = sources.GRID  # "8h"


def _to_ms(date_str):
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _last(con, sql, params, default):
    v = con.execute(sql, params).fetchone()[0]
    return v if v is not None else default


def sync(con, symbol, start_ms):
    """Bring one symbol's 8h spot + mark klines and funding up to now, resuming
    from the last stored bar. Upsert dedups, so re-fetching the last bar is fine."""
    now = int(time.time() * 1000)
    k = _last(con, "SELECT MAX(open_time) FROM klines WHERE symbol=? AND interval=?",
              (symbol, _INTERVAL), start_ms)
    store.upsert_klines(con, binance.to_rows(
        symbol, _INTERVAL, binance.fetch_raw(symbol, _INTERVAL, k, now)))
    mk = _INTERVAL + "_mark"
    m = _last(con, "SELECT MAX(open_time) FROM klines WHERE symbol=? AND interval=?",
              (symbol, mk), start_ms)
    store.upsert_klines(con, binance.to_rows(
        symbol, mk, funding.fetch_mark(symbol, _INTERVAL, m, now)))
    fs = _last(con, "SELECT MAX(funding_time) FROM funding WHERE symbol=?",
               (symbol,), start_ms)
    store.upsert_funding(con, funding.to_rows(symbol, funding.fetch_raw(symbol, fs, now)))


def cycle(con, symbols, gen, backend, rounds, fee, asof, report_dir):
    """Load stored data, run the usage->improvement loop, write a JSON report."""
    data = {}
    for sym in symbols:
        b = sources.load(con, sym, asof)
        if b["price"]:
            data[sym] = (b, metrics.equity_returns(benchmark.buy_and_hold(b["price"], fee=fee)))
    if not data:
        raise SystemExit("no stored price for any symbol -- ingest first (drop --no-ingest)")
    hist = loop.run(con, data, gen, rounds, fee, asof)
    # every gate-accepted spec is frozen into the live book (immutable) and marked forward
    for h in hist:
        name, params = h["spec"]
        for r in h["rows"]:
            if r["accept"]:
                live.freeze(con, f"sig:{signals.fmt(name, params)}:{r['symbol']}", "signal",
                            {"name": name, "params": list(params), "symbol": r["symbol"]},
                            fee, asof)
    live_rows = live.mark(con, asof)
    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "asof_ms": asof,
        "symbols": list(data),
        "backend": backend,
        "rounds": rounds,
        "fee": fee,
        "n_trials": trials.count(con),
        "digest": trials.digest(con),
        "accepted": [[list(h["spec"]), r["symbol"]] for h in hist for r in h["rows"] if r["accept"]],
        "proposals": [{"spec": list(h["spec"]), "mean_sr": h["mean_sr"]} for h in hist],
        "live": live_rows,
    }
    os.makedirs(report_dir, exist_ok=True)
    name = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    path = os.path.join(report_dir, name + ".json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    return path, report


def _gen(backend):
    if backend == "llm":
        return generator.LLMGenerator()
    if backend == "deepseek":
        return generator.DeepSeekGenerator()
    return generator.OfflineGenerator()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    p.add_argument("--backend", choices=["offline", "llm", "deepseek"], default="offline")
    p.add_argument("--rounds", type=int, default=8)
    p.add_argument("--fee", type=float, default=0.001)
    p.add_argument("--budget", type=float, default=2.0, help="daily LLM budget, USD")
    p.add_argument("--asof", default=None, help="UTC date YYYY-MM-DD (default: now)")
    p.add_argument("--start", default=_DEFAULT_START, help="kline backfill floor (UTC date)")
    p.add_argument("--no-ingest", action="store_true", help="skip the network; run on stored data")
    p.add_argument("--report-dir", default=_REPORT_DIR)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    symbols = a.symbols.split(",")
    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    if not a.no_ingest:
        start_ms = _to_ms(a.start)
        for sym in symbols:
            sync(con, sym, start_ms)
            print(f"  synced {sym}")
    gen = _gen(a.backend)
    gen.cost_con = con
    gen.budget_usd = a.budget
    path, rep = cycle(con, symbols, gen, a.backend, a.rounds, a.fee,
                      asof, a.report_dir)
    print(f"\nN={rep['n_trials']}  accepted={len(rep['accepted'])}  report -> {path}")
    for spec, sym in rep["accepted"]:
        print(f"  ACCEPT {spec} {sym}")
    for r in rep["live"]:
        print(f"  LIVE {r['book']:<22} backtest {r['bt_sr']:+.4f}  "
              f"forward {r['live_sr']:+.4f} (n={r['forward_n']})")


if __name__ == "__main__":
    main()
