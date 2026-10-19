"""State-aware bounded planner (MetaGPT Planner, stripped to qnl's actions).

One cycle looks at stored state and schedules at most `max_tasks` tasks from
registered planners, in order:
  - ingest a symbol whose 8h klines are missing/stale,
  - research one slot per symbol.
A plugin adds a task kind with register_task(); the gate and ledger are
untouched. Tasks are re-derived every run, so a crash just loses the rest of
that cycle; nothing is persisted.

    python3 -m qnl.planner --symbols BTCUSDT,ETHUSDT,SOLUSDT --backend deepseek
"""
import argparse
import datetime as dt
import os
import sqlite3

from qnl import app, autoresearch, cost, generator, store, trials

_DAY = 86_400_000
_STALE_MS = 26 * 3_600_000  # one missed 8h bar + grace
_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")

TASKS = []   # [(order, kind)]
_PLAN = {}
_RUN = {}


def register_task(kind, plan_fn, run_fn, order=50):
    """plan_fn(con, symbols, asof) -> [task dicts]; run_fn(con, task, gen, asof)
    -> bool. Lower `order` runs first."""
    _PLAN[kind] = plan_fn
    _RUN[kind] = run_fn
    TASKS.append((order, kind))
    TASKS.sort()
    return kind


def _last(con, symbol):
    try:
        row = con.execute(
            "SELECT MAX(open_time) FROM klines WHERE symbol=? AND interval=?",
            (symbol, "8h")).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _stale(last, asof, max_age_ms=_STALE_MS):
    return last is None or asof - last > max_age_ms


def _plan_ingest(con, symbols, asof):
    return [{"kind": "ingest", "symbol": s} for s in symbols
            if _stale(_last(con, s), asof)]


def _run_ingest(con, task, gen, asof):
    last = _last(con, task["symbol"])
    start = max(0, (last if last is not None else asof - 90 * _DAY) + 1)
    app.sync(con, task["symbol"], start)
    return True


def _plan_research(con, symbols, asof):
    return [{"kind": "research", "symbol": s} for s in symbols]


def _run_research(con, task, gen, asof):
    return autoresearch.step(con, task["symbol"], gen, asof)


register_task("ingest", _plan_ingest, _run_ingest, order=10)
register_task("research", _plan_research, _run_research, order=90)


def plan(con, symbols, asof, max_tasks=5, ingest=True):
    """-> [{"kind": ..., "symbol": optional}], in registration order."""
    tasks = []
    for _order, kind in TASKS:
        if kind == "ingest" and not ingest:
            continue
        tasks += _PLAN[kind](con, symbols, asof)
    return tasks[:max_tasks]


def run(con, tasks, gen, asof):
    """Execute tasks in order; a failed task is logged and skipped, never fatal."""
    out = []
    failed = set()  # a symbol whose ingest failed is quarantined for this cycle
    for t in tasks:
        if t["kind"] != "ingest" and t.get("symbol") in failed:
            print(f"planner skipping {t['kind']} {t['symbol']}: ingest failed")
            out.append((t, False))
            continue
        try:
            ok = _RUN[t["kind"]](con, t, gen, asof)
        except cost.BudgetExceeded as e:
            trials.reason(con, "planner:budget")
            print(f"planner stopping: {e}")
            break
        except KeyError:
            raise
        except Exception as e:  # noqa: BLE001 -- one task must not kill the cycle
            trials.reason(con, f"planner:{t['kind']}")
            print(f"planner {t['kind']} {t.get('symbol', '')} failed: "
                  f"{type(e).__name__}: {e}")
            if t["kind"] == "ingest":
                failed.add(t["symbol"])
            ok = False
        out.append((t, ok))
    return out


def _selfcheck():
    assert _stale(None, 10)
    assert _stale(0, 2 * _STALE_MS)
    assert not _stale(2 * _STALE_MS, 2 * _STALE_MS)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE klines(symbol TEXT, interval TEXT, open_time INTEGER)")
    con.executemany("INSERT INTO klines VALUES(?,?,?)",
                    [("BTCUSDT", "8h", 2 * _STALE_MS), ("ETHUSDT", "8h", 0)])
    tasks = plan(con, ["BTCUSDT", "ETHUSDT"], 2 * _STALE_MS, max_tasks=9)
    assert [t["kind"] for t in tasks].count("ingest") == 1, tasks
    assert tasks[-1]["kind"] == "research", tasks
    assert len(plan(con, ["BTCUSDT", "ETHUSDT"], 2 * _STALE_MS, 1)) == 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--backend", choices=["deepseek", "llm"], default="deepseek")
    p.add_argument("--max-tasks", type=int, default=5)
    p.add_argument("--budget", type=float, default=2.0, help="daily LLM budget, USD")
    p.add_argument("--no-ingest", action="store_true",
                   help="research on stored data only (no Binance catch-up)")
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    _selfcheck()
    symbols = a.symbols.split(",")
    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    tasks = plan(con, symbols, asof, a.max_tasks, ingest=not a.no_ingest)
    print(f"plan ({len(tasks)} tasks): " + ", ".join(
        f"{t['kind']}{':' + t['symbol'] if t.get('symbol') else ''}"
        for t in tasks))
    print(trials.audit(con))
    gen = (generator.DeepSeekGenerator() if a.backend == "deepseek"
           else generator.LLMGenerator())
    gen.cost_con = con
    gen.budget_usd = a.budget
    for t, ok in run(con, tasks, gen, asof):
        print(f"  {'done' if ok else 'not done'} "
              f"{t['kind']} {t.get('symbol', '')}".rstrip())
    print(trials.audit(con))
    print(cost.report(con))


if __name__ == "__main__":
    main()
