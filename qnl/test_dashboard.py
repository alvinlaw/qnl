"""dashboard self-check: the page shows the DB rows it reads.
    python3 -m qnl.test_dashboard"""
import os
import tempfile

from qnl import dashboard, live, paper, runs, store

_STEP = 28_800_000


def main():
    path = os.path.join(tempfile.mkdtemp(), "t.sqlite")
    con = store.connect(path)
    con.executescript(live._SCHEMA)
    paper.connect(con)
    runs.connect(con)
    store.upsert_klines(con, [
        ("BTCUSDT", "8h", i * _STEP, (i + 1) * _STEP,
         100.0, 100.0, 100.0, 100.0, 1.0) for i in range(20)])
    con.execute("INSERT INTO live(book,kind,spec,frozen_n,fee,created)"
                " VALUES(?,?,?,?,?,?)",
                ("sig:X", "signal", "{}", 10, 0.001, "2026-10-01T00:00:00+00:00"))
    con.execute("INSERT INTO paper(book,kind,spec,capital,frozen_n,fee,created)"
                " VALUES(?,?,?,?,?,?,?)",
                ("demo_book", "signal", "{}", 10000.0, 5, 0.0009,
                 "2026-10-01T00:00:00+00:00"))
    con.execute("INSERT INTO paper_marks(book,asof_ms,n,equity,ret,sr,mdd)"
                " VALUES(?,?,?,?,?,?,?)",
                ("demo_book", store.asof_ms(), 8, 10123.0, 0.0123, 0.5, -0.01))
    runs.record(con, "tick", "ok", "all good")
    con.commit()

    out = dashboard.render(path)
    for needle in ("demo_book", "10,123.00", "+1.23%", "sig:X", "all good",
                   "BTCUSDT"):
        assert needle in out, f"missing {needle!r}"
    assert 'class="ok"' in out
    print("dashboard self-check OK")


if __name__ == "__main__":
    main()
