"""The research spine: sweep every signal x every symbol, gate each once through
the SHARED trials ledger, and flag alpha decay (is the edge weaker in recent eras?).

    python3 -m qnl.research --symbols BTCUSDT,ETHUSDT,SOLUSDT
"""
import argparse
import os

from qnl import decay, signals, sources, stats, store, trials

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_ACCEPT_DSR = 0.95  # P[edge > best-of-N luck]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT")
    p.add_argument("--asof", default=None, help="UTC date YYYY-MM-DD (default: now)")
    p.add_argument("--fee", type=float, default=0.0009)
    p.add_argument("--folds", type=int, default=6)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    bundles = {}
    for sym in a.symbols.split(","):
        b = sources.load(con, sym, asof)
        if not b["price"] and not b["funding"]:
            raise SystemExit(f"no data for {sym} asof that date")
        bundles[sym] = b

    specs = signals.candidates()
    print(f"{a.symbols}  grid={sources.GRID}  fee={a.fee}  folds={a.folds}  candidates={len(specs)}")
    accepted, flagged = [], []
    for name, params in specs:
        label = signals.fmt(name, params)
        cells, ok, dec = [], True, False
        for sym, b in bundles.items():
            rets = signals.build(name, params, a.fee)(b)
            sr = stats.sharpe_period(rets)
            trials.record(con, label, sym, sr, len(rets), asof, fee=a.fee)
            n, var = trials.count(con), trials.variance(con)
            d0 = stats.dsr(rets, 0.0, n, var)
            _, is_dec = decay.verdict(rets, a.folds)
            ok = ok and sr > 0 and d0 >= _ACCEPT_DSR
            dec = dec or is_dec
            cells.append(f"{sym[:3]} {sr:+.2f}/{d0:.2f}{'~' if is_dec else ' '}")
        print(f"  {label:<22} {' '.join(cells)}  {'ACCEPT' if ok else '      '} {'decay' if dec else ''}")
        if ok:
            accepted.append(label)
        if dec:
            flagged.append(label)

    print(f"\naccepted {len(accepted)}/{len(specs)} (sr>0 and DSR>={_ACCEPT_DSR} on every asset);"
          f" decaying-flagged {len(flagged)}")
    for lb in accepted:
        print("  ACCEPT", lb)


if __name__ == "__main__":
    main()
