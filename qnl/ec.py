"""Equity-curve trading overlay.

Keep a strategy's return only while the strategy's own shadow equity is above
its trailing `ma`-bar mean and within a `dd` drawdown limit; otherwise sit flat.
The shadow equity always follows the unfiltered strategy, so a filter that goes
flat can still see the strategy recover and switch back on. Every decision reads
the path strictly before the return, so the overlay is PIT-clean.
"""


def overlay(rets, ma=0, dd=0.0):
    out, hist, shadow, peak = [], [], 1.0, 1.0
    for x in rets:
        hist.append(shadow)
        keep = not ma or len(hist) < ma or shadow > sum(hist[-ma:]) / ma
        if keep and dd and shadow < peak * (1.0 - dd):
            keep = False
        out.append(x if keep else 0.0)
        shadow *= 1.0 + x
        peak = max(peak, shadow)
    return out
