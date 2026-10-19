"""Strategy families — one contract: decide(closes_so_far) -> weight in [0,1].

Long-only spot, no leverage, no shorts. A spec is (family_name, params tuple);
build it with FAMILIES[name](*params).
"""
import math

_TRADING_DAYS = 365


def sma_cross(fast, slow):
    def decide(c):
        if len(c) < slow:
            return 0.0
        return 1.0 if sum(c[-fast:]) / fast > sum(c[-slow:]) / slow else 0.0
    return decide


def momentum(lookback):
    def decide(c):
        if len(c) <= lookback:
            return 0.0
        return 1.0 if c[-1] > c[-1 - lookback] else 0.0
    return decide


def mean_revert(window, z):
    """Long when price is z sd below its trailing mean (buy the dip)."""
    def decide(c):
        if len(c) < window:
            return 0.0
        seg = c[-window:]
        m = sum(seg) / window
        sd = math.sqrt(sum((x - m) ** 2 for x in seg) / window)
        if sd == 0:
            return 0.0
        return 1.0 if c[-1] < m - z * sd else 0.0
    return decide


def vol_target(window, target_ann):
    """Always long, sized so realized vol tracks target_ann (capped at 1x)."""
    def decide(c):
        if len(c) <= window:
            return 0.0
        r = [c[i] / c[i - 1] - 1 for i in range(len(c) - window, len(c))]
        m = sum(r) / len(r)
        sd = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))
        ann = sd * math.sqrt(_TRADING_DAYS)
        return min(1.0, target_ann / ann) if ann > 0 else 0.0
    return decide


FAMILIES = {
    "sma_cross": sma_cross,
    "momentum": momentum,
    "mean_revert": mean_revert,
    "vol_target": vol_target,
}

GRID = [(f, s) for f in (5, 10, 20, 30, 50) for s in (20, 50, 100, 200) if f < s]


def default_specs():
    """The offline generator's starting search space."""
    specs = [("sma_cross", (f, s)) for f, s in GRID]
    specs += [("momentum", (lb,)) for lb in (5, 10, 20, 50, 100)]
    specs += [("mean_revert", (w, z)) for w in (10, 20, 50) for z in (0.5, 1.0, 1.5, 2.0)]
    specs += [("vol_target", (w, t)) for w in (10, 20, 50) for t in (0.3, 0.5, 0.8)]
    return specs


def build(spec):
    name, params = spec
    return FAMILIES[name](*params)


def fmt(spec):
    name, params = spec
    return f"{name}({','.join(map(str, params))})"
