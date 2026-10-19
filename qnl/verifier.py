"""Stage 3: the deflation gate — deterministic, no LLM.

Given that N strategies have been evaluated (the trials ledger), is this one's
Sharpe distinguishable from the best-of-N luck?

    python3 -m qnl.verifier --symbol BTCUSDT --family sma_cross --params 20,50
"""
import argparse
import os

from qnl import backtest, benchmark, metrics, stats, store, strategies, trials, walk_forward

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")


def fold_sharpes(closes, decide, folds):
    """Per-period Sharpe of the same fixed rule within each contiguous fold.

    Not a train/test walk-forward — the params are fixed, so this is a
    stability check: does the edge hold across eras, or live in one regime?
    """
    n = len(closes)
    step = n // folds
    out = []
    for k in range(folds):
        lo = k * step
        hi = n if k == folds - 1 else (k + 1) * step
        seg = closes[lo:hi]
        if len(seg) < 2:
            out.append(0.0)
            continue
        out.append(stats.sharpe_period(metrics.equity_returns(backtest.run(seg, decide))))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--family", default="sma_cross")
    p.add_argument("--params", default="20,50")
    p.add_argument("--asof", default=None, help="UTC date YYYY-MM-DD (default: now)")
    p.add_argument("--folds", type=int, default=6)
    p.add_argument("--embargo", type=int, default=200, help="bars dropped at each test boundary")
    p.add_argument("--fee", type=float, default=0.001, help="per-side taker fee (default 10bps)")
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    rows = store.load_asof(con, a.symbol, a.interval, asof)
    if not rows:
        raise SystemExit("no bars asof that date")
    closes = [r[5] for r in rows]

    spec = (a.family, backtest.parse_params(a.params))
    decide = strategies.build(spec)
    strat = metrics.equity_returns(backtest.run(closes, decide, fee=a.fee))
    bench = metrics.equity_returns(benchmark.buy_and_hold(closes, fee=a.fee))

    trials.record(con, strategies.fmt(spec), a.symbol, stats.sharpe_period(strat),
                  len(strat), asof, fee=a.fee)

    n, var = trials.count(con), trials.variance(con)
    sr_s, sr_b = stats.sharpe_period(strat), stats.sharpe_period(bench)
    d_zero = stats.dsr(strat, 0.0, n, var)
    d_bench = stats.dsr(strat, sr_b, n, var)

    print(f"{strategies.fmt(spec)} {a.symbol} {a.interval}  bars={len(strat)}  trials N={n}")
    print(f"  per-period SR  strategy {sr_s:+.4f}   benchmark {sr_b:+.4f}")
    print(f"  DSR vs 0       {d_zero:.4f}   (P[edge > best-of-N luck])")
    print(f"  DSR vs b&h     {d_bench:.4f}   (P[beats buy-and-hold])")
    print("  fold SR        " + "  ".join(f"{f:+.3f}" for f in fold_sharpes(closes, decide, a.folds)))

    wf = list(walk_forward.walk_forward(closes, a.folds, a.embargo, a.fee))
    if wf:
        is_m = sum(w[2] for w in wf) / len(wf)
        oos_m = sum(w[3] for w in wf) / len(wf)
        print(f"  WF holdout     IS {is_m:+.4f} -> OOS {oos_m:+.4f}   (selection gap {oos_m - is_m:+.4f})")
        print("  WF picks       " + "  ".join(f"{strategies.fmt(p)}:{o:+.3f}" for _, p, _, o in wf))
    if n < 2:
        print("  NOTE: N<2 -> deflation inactive; DSR is just in-sample PSR. Sweep configs first.")


if __name__ == "__main__":
    main()
