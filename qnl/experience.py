"""Persistent research lessons, recalled by a stdlib TF-IDF index.

One row per attempt (codegen reject, gate reject, or accept). `recall` returns
the closest past lessons so the next proposer does not re-spend a slot on a dead
direction. No Sharpe/DSR numbers are stored or recalled: the point is direction
memory, not optimising against the gate.

    python3 -m qnl.experience --query "funding carry turnover" --k 8
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sqlite3
from collections import Counter

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS experience (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  params TEXT NOT NULL,
  planes TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  lesson TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS experience_name ON experience(name);
"""
_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[一-鿿]")


def _ensure(con):
    con.executescript(_SCHEMA)
    con.commit()


def record(con, kind, name, params, planes=(), verdict="", reason="",
           lesson="", ts=None):
    _ensure(con)
    con.execute(
        "INSERT INTO experience(kind,name,params,planes,verdict,reason,lesson,ts)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (kind, name, json.dumps(params, sort_keys=True, default=str),
         ",".join(planes), verdict, reason or "", lesson or "",
         ts or dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()


def _tokens(text):
    s = str(text).lower()
    cjk = _CJK.findall(s)
    return _WORD.findall(s) + cjk + [a + b for a, b in zip(cjk, cjk[1:])]


def _dict(row):
    keys = ("id", "kind", "name", "params", "planes", "verdict", "reason",
            "lesson", "ts")
    return dict(zip(keys, row))


def recall(con, query, k=6):
    """Top-k past lessons by TF-IDF cosine; recent rows fill short results."""
    _ensure(con)
    rows = con.execute(
        "SELECT id,kind,name,params,planes,verdict,reason,lesson,ts"
        " FROM experience ORDER BY id DESC").fetchall()
    if not rows:
        return []
    docs = [_tokens(" ".join((r[2], r[3], r[4], r[6], r[7]))) for r in rows]
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    q = Counter(_tokens(query))
    if not q:
        return [_dict(r) for r in rows[:k]]
    scored = []
    for i, (row, doc) in enumerate(zip(rows, docs)):
        tf = Counter(doc)
        norm = math.sqrt(sum(
            ((1 + math.log(c)) * (math.log((n + 1) / (df[t] + 1)) + 1)) ** 2
            for t, c in tf.items())) or 1.0
        score = sum(
            (1 + math.log(tf[t])) * (math.log((n + 1) / (df[t] + 1)) + 1)
            * (1 + math.log(c))
            for t, c in q.items() if t in tf)
        if score > 0:
            scored.append((score / norm, -i, row))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = [_dict(r) for _, _, r in scored[:k]]
    if len(out) < k:
        seen = {r["id"] for r in out}
        out += [_dict(r) for r in rows if r[0] not in seen][:k - len(out)]
    return out


def format(rows):
    """One prompt line per lesson; never includes P&L numbers."""
    return "\n".join(
        f"- [{r['verdict']}] {r['name']}{r['params']} planes={r['planes']}"
        f" :: {r['reason'] or r['lesson']}" for r in rows)


def _selfcheck():
    con = sqlite3.connect(":memory:")
    record(con, "codegen", "sma_tilt", [10, 50], ["price"], "gate",
           "turnover too high", "short window flips daily")
    record(con, "codegen", "momentum_break", [20], ["price"], "reject",
           "inert", "threshold never reached")
    hit = recall(con, "turnover churn", 1)
    assert hit and hit[0]["name"] == "sma_tilt", hit
    assert "sma_tilt" in format(hit)
    assert recall(sqlite3.connect(":memory:"), "x") == []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", default=None)
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()
    _selfcheck()
    con = sqlite3.connect(a.db)
    rows = (recall(con, a.query, a.k) if a.query else
            [_dict(r) for r in con.execute(
                "SELECT id,kind,name,params,planes,verdict,reason,lesson,ts"
                " FROM experience ORDER BY id DESC LIMIT ?", (a.k,))])
    print(format(rows) or "(no experience yet)")


if __name__ == "__main__":
    main()
