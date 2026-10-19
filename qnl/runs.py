"""Run history: one row per service stage, additive schema only.

    python3 -m qnl.runs [--limit 20]
"""
import argparse
import datetime as dt
import os

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started  TEXT NOT NULL,
    finished TEXT,
    stage    TEXT NOT NULL,
    status   TEXT NOT NULL,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS runs_finished ON runs(finished);
"""


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def connect(con):
    con.executescript(_SCHEMA)
    return con


def start(con, stage):
    connect(con)
    cur = con.execute("INSERT INTO runs(started,stage,status) VALUES(?,?,?)",
                      (_now(), stage, "running"))
    con.commit()
    return cur.lastrowid


def finish(con, run_id, status, detail=""):
    con.execute("UPDATE runs SET finished=?, status=?, detail=? WHERE id=?",
                (_now(), status, str(detail)[:2000], run_id))
    con.commit()


def record(con, stage, status, detail="", started=None):
    connect(con)
    con.execute("INSERT INTO runs(started,finished,stage,status,detail)"
                " VALUES(?,?,?,?,?)",
                (started or _now(), _now(), stage, status, str(detail)[:2000]))
    con.commit()


def last_status(con):
    """-> {stage: row dict} using each stage's most recent finished row."""
    connect(con)
    cols = ("id", "started", "finished", "stage", "status", "detail")
    out = {}
    for row in con.execute(
            "SELECT id,started,finished,stage,status,detail FROM runs"
            " WHERE finished IS NOT NULL ORDER BY finished"):
        out[row[3]] = dict(zip(cols, row))
    return out


def recent(con, limit=20):
    connect(con)
    cols = ("id", "started", "finished", "stage", "status", "detail")
    return [dict(zip(cols, r)) for r in con.execute(
        "SELECT id,started,finished,stage,status,detail FROM runs"
        " ORDER BY id DESC LIMIT ?", (limit,))]


def newest_finished(con):
    connect(con)
    row = con.execute("SELECT MAX(finished) FROM runs").fetchone()
    return row[0] if row else None


def _selfcheck():
    import sqlite3
    con = sqlite3.connect(":memory:")
    r = start(con, "a")
    finish(con, r, "ok", "n=1")
    record(con, "b", "failed", "boom")
    st = last_status(con)
    assert st["a"]["status"] == "ok" and st["b"]["status"] == "failed", st
    assert recent(con, 1)[0]["stage"] == "b"
    assert newest_finished(con)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()
    _selfcheck()
    import sqlite3
    for r in recent(sqlite3.connect(a.db), a.limit):
        print(f"{str(r['finished'])[:19]}  {r['stage']:<10} {r['status']:<7} "
              f"{r['detail'] or ''}")


if __name__ == "__main__":
    main()
