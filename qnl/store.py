"""Point-in-time kline store (sqlite, stdlib only).

PIT rule: a bar is knowable only once it has closed. We keep close_time
and never hand back a bar whose close_time is after `asof`.
"""
import datetime as dt
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    symbol     TEXT    NOT NULL,
    interval   TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,
    close_time INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);

CREATE TABLE IF NOT EXISTS funding (
    symbol       TEXT    NOT NULL,
    funding_time INTEGER NOT NULL,
    funding_rate REAL    NOT NULL,
    PRIMARY KEY (symbol, funding_time)
);

CREATE TABLE IF NOT EXISTS series (
    symbol     TEXT    NOT NULL,
    metric     TEXT    NOT NULL,
    close_time INTEGER NOT NULL,
    value      REAL    NOT NULL,
    PRIMARY KEY (symbol, metric, close_time)
);
"""


def connect(path):
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)
    return con


def upsert_klines(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO klines"
        "(symbol,interval,open_time,close_time,open,high,low,close,volume)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()


def load_asof(con, symbol, interval, asof_ms):
    """Bars fully closed at or before asof_ms, oldest first. The PIT gate."""
    return con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume"
        " FROM klines WHERE symbol=? AND interval=? AND close_time<=?"
        " ORDER BY open_time",
        (symbol, interval, asof_ms),
    ).fetchall()


def upsert_funding(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO funding(symbol,funding_time,funding_rate)"
        " VALUES (?,?,?)",
        rows,
    )
    con.commit()


def load_funding_asof(con, symbol, asof_ms):
    """Funding rates settled at or before asof_ms, oldest first. The PIT gate."""
    return con.execute(
        "SELECT funding_time, funding_rate FROM funding"
        " WHERE symbol=? AND funding_time<=? ORDER BY funding_time",
        (symbol, asof_ms),
    ).fetchall()


def upsert_series(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO series(symbol,metric,close_time,value)"
        " VALUES (?,?,?,?)",
        rows,
    )
    con.commit()


def load_series_asof(con, symbol, metric, asof_ms):
    """Series points knowable at or before asof_ms, oldest first. The PIT gate."""
    return con.execute(
        "SELECT close_time, value FROM series"
        " WHERE symbol=? AND metric=? AND close_time<=? ORDER BY close_time",
        (symbol, metric, asof_ms),
    ).fetchall()


def asof_ms(date_str=None):
    """'YYYY-MM-DD' (UTC) or None -> epoch ms. None means now."""
    if date_str:
        d = dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp() * 1000)
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
