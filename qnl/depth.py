"""Order-book depth: 10bp executable notional and depth-aware sizing.

`cap10` is the notional you can trade within 10bp of mid on one side (the
binding side is used). Snapshots are stored as series metrics
`depth10_<venue>_usd` so evaluation can size without a live call.

shortcut: one point-in-time L2 snapshot per run; no historical depth. Upgrade
by snapshotting on a schedule (qnl.snapshot) and reading the history.
"""
import json
import urllib.request

from qnl import store

_UA = {"User-Agent": "qnl/0.1"}
BINANCE_DEPTH = "https://fapi.binance.com/fapi/v1/depth?symbol={}&limit=1000"


def cap10(levels, mid):
    """HL levels are [{'px','sz'}], Binance levels are [[px, qty]]."""
    cum = 0.0
    for lv in levels:
        p = float(lv["px"]) if isinstance(lv, dict) else float(lv[0])
        s = float(lv["sz"]) if isinstance(lv, dict) else float(lv[1])
        if abs(p - mid) / mid * 1e4 > 10:
            break
        cum += p * s
    return cum


def _mid(bids, asks):
    p = lambda lv: float(lv["px"]) if isinstance(lv, dict) else float(lv[0])
    return (p(bids[0]) + p(asks[0])) / 2


def _cap(levels):
    bids, asks = levels
    m = _mid(bids, asks)
    return min(cap10(bids, m), cap10(asks, m))


def binance_books(symbols):
    out = {}
    for sym in symbols:
        try:
            req = urllib.request.Request(BINANCE_DEPTH.format(sym), headers=_UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            out[sym] = _cap([j["bids"], j["asks"]])
        except Exception:
            continue
    return out


def put(con, venue, caps, ts):
    rows = [(sym, f"depth10_{venue}_usd", ts, cap)
            for sym, cap in caps.items()]
    store.upsert_series(con, rows)
    return len(rows)


def latest(con, venue, symbols):
    """Most recent stored cap per symbol (nothing if no snapshot)."""
    out = {}
    for sym in symbols:
        r = con.execute(
            "SELECT value FROM series WHERE symbol=? AND metric=?"
            " ORDER BY close_time DESC LIMIT 1",
            (sym, f"depth10_{venue}_usd")).fetchone()
        if r:
            out[sym] = r[0]
    return out


def sizing(book_usd, caps):
    """Equal-weight book; a coin breaches when its weight exceeds 10bp depth."""
    live = {s: c for s, c in caps.items() if c > 0}
    if not live:
        return {"n": 0, "per": [], "breach": 0, "book": book_usd}
    w = book_usd / len(live)
    per = sorted(((s, w, c) for s, c in live.items()), key=lambda x: x[2])
    return {"n": len(live), "per": per, "book": book_usd,
            "breach": sum(1 for _, notional, c in per if notional > c)}


def _selfcheck():
    assert abs(cap10([{"px": "99.99", "sz": "2"}], 100.0) - 199.98) < 1e-9
    assert abs(cap10([["99.99", "2"]], 100.0) - 199.98) < 1e-9
    assert cap10([["99.0", "1"]], 100.0) == 0.0
    s = sizing(1000.0, {"A": 600.0, "B": 400.0, "C": 0.0})
    assert s["n"] == 2 and s["breach"] == 1, s


if __name__ == "__main__":
    _selfcheck()
    print("ok")
