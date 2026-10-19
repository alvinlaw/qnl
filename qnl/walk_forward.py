"""Stage 3b: rolling walk-forward with held-out selection.

The leak that matters here is *selection* — picking the best spec by looking at
the whole series. Each step selects on the past only, then scores on the future,
with an embargo gap so boundary bars are not scored.

Classic purged k-fold is deliberately NOT built yet: these rules read only the
past, so they cannot leak forward. Purging earns its keep once labels overlap
(e.g. models predicting a forward return).
"""
import argparse
import math
import os

from qnl import backtest, metrics, signals, sources, stats, store, strategies

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_ANNUAL_BARS = 3 * 365  # 8h grid


def walk_forward(closes, folds=6, embargo=200, fee=0.001, specs=None, min_train=200):
    """Yield (fold, best_spec, in_sample_sr, oos_sr) — past picks, future scores."""
    specs = strategies.default_specs() if specs is None else specs
    n = len(closes)
    step = n // folds
    for k in range(1, folds):
        cut = k * step
        train = closes[:cut]
        test = closes[cut + embargo : cut + step]  # embargo drops the boundary bars
        if len(train) < min_train or len(test) < 30:
            continue

        def sr(seg, spec):
            return stats.sharpe_period(
                metrics.equity_returns(backtest.run(seg, strategies.build(spec), fee=fee)))

        best = max(specs, key=lambda sp: sr(train, sp))
        yield k, best, sr(train, best), sr(test, best)


def _slice(series, lo, hi):
    """Cut one series by time fraction. Planes differ in length by a bar or two
    (price 7809 vs funding 7782), so each is cut by its own length."""
    if isinstance(series, dict):
        return {k: _slice(v, lo, hi) for k, v in series.items()}
    if not series:
        return series
    n = len(series)
    return series[int(n * lo):None if hi is None else int(n * hi)]


def split(bundle, frac=0.7):
    """-> (train, test) bundles cut by time fraction, each plane by its own length."""
    return ({k: _slice(v, 0.0, frac) for k, v in bundle.items()},
            {k: _slice(v, frac, None) for k, v in bundle.items()})


def holdout(bundle, specs, frac=0.7, fee=0.001):
    """Single holdout OOS: pick the best spec on the first `frac` of every series,
    score it ONCE on the rest. -> (spec, in_sample_sr, oos_sr).

    Unlike walk_forward() this is for any signal (carry included) and spends the
    test window exactly once -- no re-selection after seeing it.
    """
    train, test = split(bundle, frac)

    def sr(b, spec):
        return stats.sharpe_period(signals.build(spec[0], spec[1], fee)(b))

    best = max(specs, key=lambda sp: sr(train, sp))
    return best, sr(train, best), sr(test, best)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--signal", default=None,
                   help="registered family (default: first sweep candidate)")
    p.add_argument("--frac", type=float, default=0.7,
                   help="fraction of the series used to select (default 0.7)")
    p.add_argument("--fee", type=float, default=0.001)
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    con = store.connect(a.db)
    bundle = sources.load(con, a.symbol, store.asof_ms(a.asof))
    signal = a.signal or signals.candidates()[0][0]
    a.signal = signal
    specs = [s for s in signals.candidates() if s[0] == signal]
    if not specs:
        raise SystemExit(f"no specs for {a.signal}")
    spec, isr, osr = holdout(bundle, specs, a.frac, a.fee)
    ann = math.sqrt(_ANNUAL_BARS)
    print(f"{a.symbol} {a.signal} frac={a.frac} fee={a.fee}")
    print(f"  best {signals.fmt(*spec)}  IS={isr * ann:+.2f}  OOS={osr * ann:+.2f} annSR")


if __name__ == "__main__":
    main()
