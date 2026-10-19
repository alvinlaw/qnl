"""Book-level portfolio construction over aligned return streams.

A stream is {day: return}. The book only consumes returns, so it can combine
any signal family without touching the gate. `walk_forward` re-estimates
weights on a trailing window only; no future data is used.
"""
import math
import statistics


def align(streams):
    """-> names, days, rows (name -> returns aligned to days)."""
    names = list(streams)
    if not names:
        return [], [], {}
    days = sorted(set.intersection(*(set(s) for s in streams.values())))
    return names, days, {n: [streams[n][d] for d in days] for n in names}


def ann_sr(x, ppy=365):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = statistics.pstdev(x)
    return m / sd * math.sqrt(ppy) if sd > 0 else 0.0


def ann_vol(x, ppy=365):
    return statistics.pstdev(x) * math.sqrt(ppy) if len(x) > 1 else 0.0


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = sum((a - mx) ** 2 for a in x)
    sy = sum((b - my) ** 2 for b in y)
    if sx <= 0 or sy <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy)


def corr_matrix(rows):
    names = list(rows)
    return [[corr(rows[a], rows[b]) for b in names] for a in names]


def effective_n(cmat):
    """N / (1 + (N-1) * mean pairwise correlation), capped at N."""
    n = len(cmat)
    if n <= 1:
        return float(n)
    rho = sum(cmat[i][j] for i in range(n) for j in range(n)
              if i != j) / (n * (n - 1))
    denom = 1 + (n - 1) * rho
    return min(float(n), n / denom) if denom > 1e-9 else float(n)


def weights_equal(rows, ppy=365):
    names = list(rows)
    return {n: 1.0 / len(names) for n in names}


def weights_risk_parity(rows, ppy=365):
    names = list(rows)
    raw = []
    for n in names:
        v = ann_vol(rows[n], ppy)
        raw.append(1.0 / v if v > 0 else 0.0)
    s = sum(raw)
    if s <= 0:
        return weights_equal(rows)
    return {n: w / s for n, w in zip(names, raw)}


def weights_tangency(rows, ppy=365):
    names = list(rows)
    raw = []
    for n in names:
        v = ann_vol(rows[n], ppy)
        raw.append(max(ann_sr(rows[n], ppy), 0.0) / v if v > 0 else 0.0)
    s = sum(raw)
    if s <= 0:
        return weights_equal(rows)
    return {n: w / s for n, w in zip(names, raw)}


def combine(rows, weights):
    names = list(rows)
    if not names:
        return []
    return [sum(weights.get(n, 0.0) * rows[n][i] for n in names)
            for i in range(len(rows[names[0]]))]


def walk_forward(rows, weight_fn=weights_tangency, window=365, ppy=365):
    """OOS book returns: weights at t use only returns[t-window:t]."""
    names = list(rows)
    if not names:
        return []
    m = len(rows[names[0]])
    out = []
    for t in range(window, m):
        past = {n: rows[n][t - window:t] for n in names}
        w = weight_fn(past, ppy)
        out.append(sum(w[n] * rows[n][t] for n in names))
    return out


def report(rows, ppy=365):
    names = list(rows)
    cm = corr_matrix(rows)
    print(f"{'stream':<26} {'SR':>7} {'ann%':>8} {'vol%':>8}")
    for n in names:
        x = rows[n]
        print(f"{n:<26} {ann_sr(x, ppy):+7.2f} "
              f"{sum(x) / len(x) * ppy * 100:+8.2f} {ann_vol(x, ppy) * 100:8.2f}")
    print(f"effective N {effective_n(cm):.1f} / {len(names)}")
    print("corr:")
    for i, a in enumerate(names):
        print(f"  {a:<24} " + " ".join(f"{cm[i][j]:+.2f}" for j in range(len(names))))
    return cm
