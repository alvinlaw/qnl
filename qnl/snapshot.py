"""Forward PIT snapshots: data that cannot be backfilled.

Snapshot-only sources are appended to the same `series` store, so a real PIT
history compounds from now on. A plugin registers a provider; the scheduler
runs every registered one.

    python3 -m qnl.snapshot [--only name1,name2]
"""
import argparse
import os
import time

from qnl import store

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")

PROVIDERS = {}


def register_provider(name, fn):
    """fn(con, ts) -> rows written; may raise, the caller isolates failures."""
    PROVIDERS[name] = fn
    return name


def bucket(now=None):
    return int((now or time.time()) // 3600) * 3_600_000


def run(con, only=None):
    """Run every selected provider; one failure never kills the rest.
    -> {name: rows written, or the error string}."""
    ts = bucket()
    out = {}
    for name in (only or list(PROVIDERS)):
        fn = PROVIDERS.get(name)
        if fn is None:
            print(f"{name}: not registered")
            continue
        try:
            out[name] = fn(con, ts)
            print(f"{name}: {out[name]} rows")
        except Exception as e:  # noqa: BLE001 -- one provider must not kill the rest
            out[name] = f"{type(e).__name__}: {e}"
            print(f"{name} failed: {out[name]}")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", default=None,
                   help="comma-separated provider names (default: all registered)")
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()
    run(store.connect(a.db), a.only.split(",") if a.only else None)


if __name__ == "__main__":
    main()
