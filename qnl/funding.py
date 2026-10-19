"""Binance public funding-rate ingest.

Funding settles every 8h. `/fapi/v1/fundingRate` returns *settled* history keyed
by fundingTime, so it is point-in-time clean and needs no API key (stdlib
urllib). Note: this is the USDM (futures) host, not the spot host in binance.py.
"""
import bisect
import json
import time
import urllib.parse
import urllib.request

_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
_MARK_URL = "https://fapi.binance.com/fapi/v1/markPriceKlines"


def fetch_raw(symbol, start_ms, end_ms=None, limit=1000):
    """Settled funding records whose fundingTime falls in [start_ms, end_ms)."""
    out, cur = [], start_ms
    while True:
        q = {"symbol": symbol, "startTime": cur, "limit": limit}
        if end_ms is not None:
            q["endTime"] = end_ms
        req = urllib.request.Request(
            _URL + "?" + urllib.parse.urlencode(q),
            headers={"User-Agent": "qnl/0.1"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            batch = json.load(r)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < limit:
            break
        cur = int(batch[-1]["fundingTime"]) + 1
        time.sleep(0.15)
    return out


def to_rows(symbol, raw):
    return [(symbol, int(b["fundingTime"]), float(b["fundingRate"])) for b in raw]


def fetch_mark(symbol, interval, start_ms, end_ms=None, limit=1000):
    """Mark-price klines (the perp's fair value) — same shape as spot klines."""
    out, cur = [], start_ms
    while True:
        q = {"symbol": symbol, "interval": interval, "startTime": cur, "limit": limit}
        if end_ms is not None:
            q["endTime"] = end_ms
        req = urllib.request.Request(
            _MARK_URL + "?" + urllib.parse.urlencode(q),
            headers={"User-Agent": "qnl/0.1"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            batch = json.load(r)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < limit:
            break
        cur = batch[-1][0] + 1
        time.sleep(0.15)
    return out


def basis_series(funding_times, spot_bars, mark_bars):
    """perp-vs-spot basis at each funding settle, from the 8h bar closed at/before it.

    bars are (open_time, close_time, ..., close, ...) rows. Returns a list the
    same length as funding_times; None where no bar is available yet.
    """
    def _axis(bars):
        return [b[1] for b in bars], [b[5] for b in bars]
    sc_ct, sc_cl = _axis(spot_bars)
    mk_ct, mk_cl = _axis(mark_bars)
    out = []
    for t in funding_times:
        i = bisect.bisect_right(sc_ct, t) - 1
        j = bisect.bisect_right(mk_ct, t) - 1
        out.append(mk_cl[j] / sc_cl[i] - 1 if i >= 0 and j >= 0 else None)
    return out
