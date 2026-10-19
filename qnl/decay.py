"""Alpha-decay self-test: is a signal's edge weaker in recent eras than older?

The per-era Sharpe split the verifier and carry already print, factored out and
turned into a verdict so the research loop can flag decaying edges automatically.
"""
from qnl import stats


def fold_sharpes(rets, folds):
    """Per-period Sharpe within each contiguous era, oldest first."""
    n = len(rets)
    step = max(1, n // folds)
    out = []
    for k in range(folds):
        seg = rets[k * step: n if k == folds - 1 else (k + 1) * step]
        out.append(stats.sharpe_period(seg) if len(seg) >= 2 else 0.0)
    return out


def verdict(rets, folds=6, drop=0.5):
    """(per-era Sharpes, decaying?).

    Decaying when the recent half's mean Sharpe is negative, or has fallen more
    than `drop` below the older half's. `drop` is a convention, not a law.
    """
    fs = fold_sharpes(rets, folds)
    h = max(1, len(fs) // 2)
    early = sum(fs[:h]) / h
    late = sum(fs[h:]) / max(1, len(fs) - h)
    return fs, (late < 0.0 or late < early - drop)
