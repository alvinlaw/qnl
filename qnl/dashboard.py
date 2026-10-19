"""qnl.dashboard: one static HTML page rendered from SQLite.

    python3 -m qnl.dashboard [--db qnl.sqlite] [--report-dir reports]

No server and no JS: the service rewrites reports/index.html every tick. Open
the file directly, or serve the directory with `python3 -m http.server`.
"""
import argparse
import datetime as dt
import html
import os
import sqlite3

from qnl import cost, paper, store, trials

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_ROOT, "qnl.sqlite")
_REPORT_DIR = os.path.join(_ROOT, "reports")

_CSS = """body{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#111}
h1{font-size:1.2rem;margin-top:1.6rem}table{border-collapse:collapse;margin:.6rem 0}
th,td{border:1px solid #ccc;padding:4px 8px;text-align:right}
th:first-child,td:first-child{text-align:left}
.pos{color:#0a0}.neg{color:#c00}.ok{color:#0a0}.failed{color:#c00}
.skipped{color:#888}code{background:#f3f3f3;padding:0 3px}"""


def _sr(x):
    return f'<span class="{"pos" if x >= 0 else "neg"}">{x:+.4f}</span>'


def _table(headers, rows, empty="&mdash;"):
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    body = body or f'<tr><td colspan="{len(headers)}">{empty}</td></tr>'
    return f"<table><tr>{head}</tr>{body}</table>"


def _q(con, sql, params=()):
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def render(db):
    """-> HTML string for the given SQLite path."""
    con = sqlite3.connect(db)
    asof = store.asof_ms()
    now = dt.datetime.now(dt.timezone.utc).isoformat()[:19]

    runs_rows = _q(con, "SELECT finished,stage,status,detail FROM runs"
                        " WHERE finished IS NOT NULL ORDER BY id DESC LIMIT 15")
    runs_html = _table(
        ["finished", "stage", "status", "detail"],
        [(html.escape(str(f)[:19]), html.escape(s), f'<span class="{html.escape(st)}">'
          f"{html.escape(st)}</span>", html.escape(str(d or "")))
         for f, s, st, d in runs_rows])

    fresh = _q(con, "SELECT symbol, MAX(close_time) FROM klines"
                    " WHERE interval='8h' GROUP BY symbol ORDER BY symbol")
    fresh_html = _table(
        ["symbol", "latest close age"],
        [(html.escape(s), f"{max(0, asof - c) / 3_600_000:.1f}h")
         for s, c in fresh if c is not None])

    papers = paper.report(con)
    paper_html = _table(
        ["book", "kind", "n", "equity", "return", "SR", "max DD", "mark age"],
        [(html.escape(r["book"]), html.escape(r["kind"]),
          f'{r["n"]}<small> / {r["frozen_n"]}</small>',
          f'{r["equity"]:,.2f}', f'{r["ret"]:+.2%}', _sr(r["sr"]),
          f'{r["mdd"]:.2%}',
          "&mdash;" if r["age_h"] is None else f'{r["age_h"]:.1f}h')
         for r in papers])

    live_rows = _q(con, "SELECT book,kind,frozen_n,fee,created FROM live"
                        " ORDER BY book")
    live_html = _table(
        ["book", "kind", "frozen n", "fee", "created"],
        [(html.escape(b), html.escape(k), str(n), f"{fee:.4f}",
          html.escape(str(c)[:19])) for b, k, n, fee, c in live_rows])

    try:
        n = trials.count(con)
    except sqlite3.OperationalError:
        n = 0
    try:
        book_count = con.execute("SELECT COUNT(*) FROM live").fetchone()[0]
    except sqlite3.OperationalError:
        book_count = 0
    tick = _q(con, "SELECT finished,status,detail FROM runs WHERE stage='tick'"
                   " ORDER BY id DESC LIMIT 1")
    tick_html = ("&mdash;" if not tick else
                 f'{html.escape(str(tick[0][0])[:19])} '
                 f'<span class="{html.escape(tick[0][1])}">'
                 f"{html.escape(tick[0][1])}</span> "
                 f"{html.escape(str(tick[0][2] or ''))}")
    size = os.path.getsize(db) / 1e6 if os.path.exists(db) else 0.0

    return f"""<!doctype html><meta charset=utf-8><title>qnl</title>
<style>{_CSS}</style><h1>qnl</h1>
<p>generated {now} UTC &middot; <code>{html.escape(db)}</code>
({size:.0f} MB) &middot; N={n} &middot; live books={book_count}
&middot; today ${cost.today(con):.4f}</p>
<p>last tick: {tick_html}</p>
<h1>paper books</h1>{paper_html}
<h1>live books</h1>{live_html}
<h1>recent runs</h1>{runs_html}
<h1>data freshness</h1>{fresh_html}
"""


def build(db, report_dir):
    """Render the DB -> report_dir/index.html. -> path."""
    os.makedirs(report_dir, exist_ok=True)
    out = os.path.join(report_dir, "index.html")
    with open(out, "w") as fh:
        fh.write(render(db))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=_DEFAULT_DB)
    p.add_argument("--report-dir", default=_REPORT_DIR)
    a = p.parse_args()
    _db, out = build(a.db, a.report_dir)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
