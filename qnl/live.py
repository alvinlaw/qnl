"""Live book: strategies FROZEN at a point in time and marked forward on bars
that arrived after. This is the out-of-sample test DSR and walk-forward cannot
give -- a real forward sample on data the spec never saw.

Freezing locks the spec. Forward returns are the tail of the causal return
series past the length it had at freeze. Every registered kind must be
no-lookahead (prefix-invariant), so rets[:frozen_n] never moves and
rets[frozen_n:] is a clean forward sample.

    python3 -m qnl.live                       # mark every book
"""
import argparse
import datetime as dt
import json
import os

from qnl import signals, sources, stats, store

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS live (
    book     TEXT PRIMARY KEY,
    kind     TEXT    NOT NULL,
    spec     TEXT    NOT NULL,
    frozen_n INTEGER NOT NULL,
    fee      REAL    NOT NULL,
    created  TEXT    NOT NULL
);
"""

KINDS = {}


def register_kind(kind, fn):
    """fn(con, spec, fee, asof) -> the full causal return series for one book."""
    KINDS[kind] = fn
    return kind


def connect(path):
    con = store.connect(path)
    con.executescript(_SCHEMA)
    return con


def _signal(con, spec, fee, asof):
    b = sources.load(con, spec["symbol"], asof)
    return signals.build(spec["name"], tuple(spec["params"]), fee)(b)


register_kind("signal", _signal)


def series(con, kind, spec, fee, asof):
    """The full causal return series for one registered book kind."""
    if kind not in KINDS:
        raise ValueError(f"unknown book kind: {kind!r}")
    return KINDS[kind](con, spec, fee, asof)


_series = series  # existing callers/tests


def freeze(con, book, kind, spec, fee, asof):
    """Freeze a book at its current series length. Immutable: a second freeze of
    the same book keeps the first line, so forward stays out-of-sample."""
    con.executescript(_SCHEMA)
    n = len(_series(con, kind, spec, fee, asof))
    con.execute("INSERT OR IGNORE INTO live(book,kind,spec,frozen_n,fee,created)"
                " VALUES(?,?,?,?,?,?)",
                (book, kind, json.dumps(spec), n, fee,
                 dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()
    return con.execute("SELECT frozen_n FROM live WHERE book=?", (book,)).fetchone()[0]


def mark(con, asof):
    """Every book: backtest SR over the whole series vs the forward SR since freeze."""
    con.executescript(_SCHEMA)
    rows = []
    for book, kind, spec, frozen_n, fee, _created in con.execute(
            "SELECT book,kind,spec,frozen_n,fee,created FROM live ORDER BY book"):
        rets = _series(con, kind, json.loads(spec), fee, asof)
        fwd = rets[frozen_n:]
        rows.append({"book": book, "kind": kind, "frozen_n": frozen_n,
                     "total_n": len(rets), "forward_n": len(fwd),
                     "bt_sr": stats.sharpe_period(rets),
                     "live_sr": stats.sharpe_period(fwd)})
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fee", type=float, default=0.0009)
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = connect(a.db)
    for r in mark(con, asof):
        print(f"  {r['book']:<22} frozen@{r['frozen_n']:<5} "
              f"backtest {r['bt_sr']:+.4f}  LIVE {r['live_sr']:+.4f}  (fwd n={r['forward_n']})")


if __name__ == "__main__":
    main()
