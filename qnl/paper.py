"""Paper book: a capital-weighted forward simulation of a live book.

A paper book freezes the same causal series as `live` and tracks equity from
the freeze point using returns the spec never saw. Marks are appended only when
the series length advances, so repeated ticks are idempotent. Simulation only:
no orders, no fills; fees are already inside each series.

    python3 -m qnl.paper --seed book kind fee capital
    python3 -m qnl.paper                          # mark every book
"""
import argparse
import datetime as dt
import json
import math
import os

from qnl import live, stats, store

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper (
    book     TEXT PRIMARY KEY,
    kind     TEXT    NOT NULL,
    spec     TEXT    NOT NULL,
    capital  REAL    NOT NULL,
    frozen_n INTEGER NOT NULL,
    fee      REAL    NOT NULL,
    created  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_marks (
    book    TEXT    NOT NULL,
    asof_ms INTEGER NOT NULL,
    n       INTEGER NOT NULL,
    equity  REAL    NOT NULL,
    ret     REAL    NOT NULL,
    sr      REAL    NOT NULL,
    mdd     REAL    NOT NULL,
    PRIMARY KEY (book, asof_ms)
);
"""


def connect(con):
    con.executescript(_SCHEMA)
    return con


def _mdd(rets):
    """Max drawdown of the compounded equity built from `rets`."""
    peak = eq = 1.0
    worst = 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return worst


def seed(con, book, kind, spec, fee, capital=10_000.0, asof=None):
    """Freeze a paper book once; later calls return the existing frozen_n."""
    connect(con)
    row = con.execute("SELECT frozen_n FROM paper WHERE book=?", (book,)).fetchone()
    if row:
        return row[0]
    n = len(live.series(con, kind, spec, fee, asof or store.asof_ms()))
    con.execute("INSERT INTO paper(book,kind,spec,capital,frozen_n,fee,created)"
                " VALUES(?,?,?,?,?,?,?)",
                (book, kind, json.dumps(spec), capital, n, fee,
                 dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()
    return n


def mark(con, asof):
    """Append one mark per book whose series advanced. -> [mark dicts]."""
    connect(con)
    out = []
    for book, kind, spec, capital, frozen_n, fee, _created in con.execute(
            "SELECT book,kind,spec,capital,frozen_n,fee,created FROM paper"
            " ORDER BY book"):
        rets = live.series(con, kind, json.loads(spec), fee, asof)
        n = len(rets)
        last = con.execute("SELECT MAX(n) FROM paper_marks WHERE book=?",
                           (book,)).fetchone()[0]
        if n <= max(frozen_n, last or 0):
            continue
        fwd = rets[frozen_n:n]
        equity = capital * math.exp(sum(math.log1p(r) for r in fwd))
        row = {"book": book, "asof_ms": asof, "n": n, "equity": equity,
               "ret": equity / capital - 1.0,
               "sr": stats.sharpe_period(fwd), "mdd": _mdd(fwd)}
        con.execute("INSERT OR REPLACE INTO paper_marks"
                    "(book,asof_ms,n,equity,ret,sr,mdd) VALUES(?,?,?,?,?,?,?)",
                    (book, asof, n, equity, row["ret"], row["sr"], row["mdd"]))
        out.append(row)
    con.commit()
    return out


def report(con):
    """One row per book with its latest mark and mark age in hours."""
    connect(con)
    rows = []
    for book, kind, capital, frozen_n in con.execute(
            "SELECT book,kind,capital,frozen_n FROM paper ORDER BY book"):
        m = con.execute(
            "SELECT asof_ms,n,equity,ret,sr,mdd FROM paper_marks"
            " WHERE book=? ORDER BY asof_ms DESC LIMIT 1", (book,)).fetchone()
        if not m:
            rows.append({"book": book, "kind": kind, "capital": capital,
                         "frozen_n": frozen_n, "equity": capital, "ret": 0.0,
                         "sr": 0.0, "mdd": 0.0, "n": frozen_n, "age_h": None})
            continue
        age = (store.asof_ms() - m[0]) / 3_600_000
        rows.append({"book": book, "kind": kind, "capital": capital,
                     "frozen_n": frozen_n, "equity": m[2], "ret": m[3],
                     "sr": m[4], "mdd": m[5], "n": m[1], "age_h": age})
    return rows


def _selfcheck():
    con = store.connect(":memory:")
    store.upsert_klines(con, [
        ("X", "8h", i * 28_800_000, (i + 1) * 28_800_000,
         100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1.0) for i in range(400)])
    spec = {"name": "sma_cross", "params": [5, 20], "symbol": "X"}
    n0 = seed(con, "sig:X", "signal", spec, 0.001, capital=1000.0,
              asof=300 * 28_800_000)
    assert seed(con, "sig:X", "signal", spec, 0.001,
                asof=399 * 28_800_000) == n0, "seed must be immutable"
    first = mark(con, 399 * 28_800_000)
    assert first and first[0]["n"] > n0
    assert mark(con, 399 * 28_800_000) == [], "same series length is not re-marked"
    r = report(con)[0]
    assert abs(r["equity"] - first[0]["equity"]) < 1e-9
    assert r["mdd"] <= 0.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seed", nargs=3, metavar=("BOOK", "KIND", "FEE"))
    p.add_argument("--spec", default="{}", help="JSON spec for --seed")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()
    asof = store.asof_ms(a.asof)
    con = connect(store.connect(a.db))
    if a.seed:
        n = seed(con, a.seed[0], a.seed[1], json.loads(a.spec),
                 float(a.seed[2]), a.capital, asof)
        print(f"seeded {a.seed[0]} frozen@{n}")
        return
    for r in report(con):
        print(f"  {r['book']:<22} n={r['n']:<5} eq={r['equity']:>10.2f} "
              f"ret={r['ret']:+.2%} sr={r['sr']:+.3f} mdd={r['mdd']:.2%}")


if __name__ == "__main__":
    main()
