"""Approximate LLM spend accounting with a hard daily budget.

The generator records token usage after each call; the planner checks the
budget before each call. Prices are a small hardcoded table (USD per 1M
tokens) -- an unknown model is charged at Sonnet prices, deliberately high so
the budget errs on the safe side. This is accounting, not billing.

    python3 -m qnl.cost --days 7
"""
import argparse
import datetime as dt
import os
import sqlite3

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cost (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  backend TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  usd REAL NOT NULL
);
"""
_PRICES = {  # (input, output) USD per 1M tokens
    "deepseek-chat": (0.28, 0.42),
    "deepseek-reasoner": (0.28, 0.42),
    "claude-haiku": (1.0, 5.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (15.0, 75.0),
}
_HIGH = (3.0, 15.0)  # unknown model: assume Sonnet, never undercount


class BudgetExceeded(RuntimeError):
    pass


def _ensure(con):
    con.executescript(_SCHEMA)
    con.commit()


def _price(model):
    for prefix, price in _PRICES.items():
        if model.startswith(prefix):
            return price
    return _HIGH


def record(con, backend, model, prompt_tokens, completion_tokens):
    _ensure(con)
    pricing = _price(model)
    usd = (prompt_tokens * pricing[0] + completion_tokens * pricing[1]) / 1e6
    con.execute("INSERT INTO llm_cost(ts,backend,model,prompt_tokens,"
                "completion_tokens,usd) VALUES(?,?,?,?,?,?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(), backend, model,
                 int(prompt_tokens), int(completion_tokens), usd))
    con.commit()


def spent(con, since=None):
    _ensure(con)
    if since:
        row = con.execute("SELECT COALESCE(SUM(usd),0) FROM llm_cost"
                          " WHERE ts>=?", (since,)).fetchone()
    else:
        row = con.execute("SELECT COALESCE(SUM(usd),0) FROM llm_cost").fetchone()
    return float(row[0] or 0.0)


def today(con):
    start = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    return spent(con, start)


def report(con, days=7):
    _ensure(con)
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=days)).isoformat()
    row = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0),"
        " COALESCE(SUM(completion_tokens),0), COALESCE(SUM(usd),0)"
        " FROM llm_cost WHERE ts>=?", (since,)).fetchone()
    return (f"llm cost: {row[0]} calls, {row[1]} in + {row[2]} out tokens, "
            f"${row[3]:.4f} in last {days}d (today ${today(con):.4f})")


def _selfcheck():
    con = sqlite3.connect(":memory:")
    record(con, "deepseek", "deepseek-chat", 1_000_000, 0)
    assert abs(spent(con) - 0.28) < 1e-9, spent(con)
    record(con, "anthropic", "unknown-model", 0, 1_000_000)
    assert abs(spent(con) - 15.28) < 1e-9, spent(con)
    assert "calls" in report(con)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()
    _selfcheck()
    print(report(sqlite3.connect(a.db), a.days))


if __name__ == "__main__":
    main()
