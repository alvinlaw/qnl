"""Data-plane registry: each source turns the PIT store into one named series.

A plane is added by writing a loader and registering it; the gate, ledger and
decay logic downstream never change. The canonical grid is 8h (the funding
grid); every plane is placed on that grid by timestamp, with None where the
source has no bar -- never a carried-forward value.
"""
import bisect

from qnl import funding, store

GRID = "8h"
MARK_GRID = "8h_mark"
_DAY = 86_400_000
_IV = 8 * 3_600_000


def _snap(pts):
    """Binance fundingTime sometimes lands 1-2ms AFTER the 8h boundary, which
    would push it into the next bar and leave its own bar empty. Snap that
    sub-second jitter back; a genuinely late point still maps forward."""
    return [((t - t % _IV, v) if t % _IV <= 1000 else (t, v)) for t, v in pts]


def _price(con, symbol, asof):
    return [r[5] for r in store.load_asof(con, symbol, GRID, asof)]


def _on_grid(times, values, grid_ct, hold_ms=0):
    """Place points on the price grid: each point belongs to the FIRST grid bar
    that closes at or after it, so it is never visible to an earlier bar. A kline
    close is 1ms before its true boundary, hence the +1: a point stamped exactly
    on the boundary maps to the bar ending there, and a sub-grid point (e.g. a 4h
    flow bar) maps to the 8h bar it closes inside. With `hold_ms` (daily sources)
    the value also fills bars closing within its validity window -- but never
    past it. Gaps and a stopped series stay None."""
    bounds = [t + 1 for t in grid_ct]
    out = [None] * len(grid_ct)
    for t, v in zip(times, values):
        j = bisect.bisect_left(bounds, t)
        end = bisect.bisect_left(bounds, t + hold_ms) if hold_ms else j + 1
        for k in range(j, min(end, len(bounds))):
            out[k] = v
    return out


def _funding(con, symbol, asof):
    bars = store.load_asof(con, symbol, GRID, asof)
    pts = _snap(store.load_funding_asof(con, symbol, asof))
    return _on_grid([p[0] for p in pts], [p[1] for p in pts], [b[1] for b in bars])


def _basis(con, symbol, asof):
    bars = store.load_asof(con, symbol, GRID, asof)
    mark = store.load_asof(con, symbol, MARK_GRID, asof)
    if not bars or not mark:
        return None
    ft = [r[0] for r in _snap(store.load_funding_asof(con, symbol, asof))]
    return _on_grid(ft, funding.basis_series(ft, bars, mark), [b[1] for b in bars])


def _plane(con, symbol, asof, metrics, src=None, hold_ms=0):
    """A {metric: series} plane on the GRID, or None if there are no grid bars.
    `src` reads the series from another symbol (GLOBAL macro) on this grid."""
    bars = store.load_asof(con, symbol, GRID, asof)
    if not bars:
        return None
    grid = [b[1] for b in bars]
    out = {}
    for metric in metrics:
        pts = store.load_series_asof(con, src or symbol, metric, asof)
        out[metric] = (_on_grid([p[0] for p in pts], [p[1] for p in pts], grid,
                                hold_ms) if pts else None)
    return out if any(v is not None for v in out.values()) else None


SOURCES = {}
DOCS = {}


def register_plane(name, fn, doc=""):
    """fn(con, symbol, asof) -> series, or None when the plane has no data.
    `doc` describes the plane for the codegen prompt."""
    SOURCES[name] = fn
    if doc:
        DOCS[name] = doc
    return name


register_plane("price", _price, '"price"   : spot closes, length N')
register_plane("funding", _funding, '"funding" : 8h funding rates')
register_plane("basis", _basis, '"basis"   : perp-spot basis')


def load(con, symbol, asof):
    """Every registered plane for one symbol, as {name: series}."""
    return {name: fn(con, symbol, asof) for name, fn in SOURCES.items()}
