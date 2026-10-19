"""Fetch public Binance spot klines. No API key needed (stdlib urllib)."""
import json
import time
import urllib.parse
import urllib.request

_URL = "https://api.binance.com/api/v3/klines"


def fetch_raw(symbol, interval, start_ms, end_ms=None, limit=1000):
    """All raw klines whose open_time falls in [start_ms, end_ms). Paginates."""
    out, cur = [], start_ms
    while True:
        q = {"symbol": symbol, "interval": interval, "startTime": cur, "limit": limit}
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
        cur = batch[-1][0] + 1
        time.sleep(0.15)
    return out


def to_rows(symbol, interval, raw):
    """Binance kline array -> store row tuple."""
    return [
        (symbol, interval, int(k[0]), int(k[6]),
         float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
        for k in raw
    ]
