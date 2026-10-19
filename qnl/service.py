"""qnl.service: the single deployable entry point.

    python3 -m qnl.service tick     # one scheduled cycle (systemd timer)
    python3 -m qnl.service status   # last run per stage, freshness, spend
    python3 -m qnl.service doctor   # config/DB/keys/disk/units sanity
    python3 -m qnl.service report   # regenerate reports/index.html

`tick` takes an exclusive flock so two timers can never overlap. Each stage is
isolated: a failure is recorded in `runs`, retried `service.retries` times, and
never aborts the remaining stages. Exit code is nonzero if any stage failed.

`--no-network` records snapshot/live/planner as `skipped` and runs only the
purely stored-data stages, so a verification tick is deterministic.
"""
import argparse
import datetime as dt
import fcntl
import logging
import logging.handlers
import os
import shutil
import sqlite3
import sys

from qnl import (config, cost, dashboard, extend, generator, live, notify,
                 paper, planner, pool, runs, snapshot, store, trials)

log = logging.getLogger("qnl")

_STAGES = []  # [(order, name, fn, needs_network)]


def register_stage(name, fn, order=50, network=True):
    """fn(con, cfg, asof) -> detail string. Plugins add stages the same way."""
    _STAGES.append((order, name, fn, network))
    _STAGES.sort()
    return name


def _setup_logging(cfg):
    os.makedirs(cfg["paths"]["reports"], exist_ok=True)
    path = os.path.join(cfg["paths"]["reports"], "qnl.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    log.handlers[:] = []
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000,
                                              backupCount=3)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return path


def _lock(path):
    """Exclusive non-blocking lock; returns an open fd, or None if held."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd


def _lock_holder(path):
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        pid = os.read(fd, 32).decode(errors="replace").strip()
        return pid or "?"
    finally:
        os.close(fd)
    return None


def _stage(con, name, fn, retries):
    last = ""
    for attempt in range(retries + 1):
        rid = runs.start(con, name)
        try:
            detail = fn() or ""
            runs.finish(con, rid, "ok", detail)
            log.info("%s ok: %s", name, detail)
            return True, detail
        except Exception as e:  # noqa: BLE001 -- stages are isolated
            last = f"{type(e).__name__}: {e}"
            runs.finish(con, rid, "failed", last)
            log.exception("%s failed (attempt %d/%d): %s", name, attempt + 1,
                          retries + 1, last)
    return False, last


def _gen(cfg):
    return generator.create(cfg)


# --- built-in stages -------------------------------------------------------

def _snapshot(con, cfg, asof):
    out = snapshot.run(con)
    return ", ".join(f"{k}={v}" for k, v in out.items())


def _live(con, cfg, asof):
    rows = live.mark(con, asof)
    return ", ".join(f"{r['book']} fwd_sr={r['live_sr']:+.3f}" for r in rows)


def _paper(con, cfg, asof):
    rows = paper.mark(con, asof)
    return (", ".join(f"{r['book']} eq={r['equity']:.0f}" for r in rows)
            or "no new marks")


def _extensions(con, cfg, asof):
    rows = extend.sync(cfg, cfg["paths"]["extensions"])
    bad = [f"{n}: {d}" for n, s, d in rows if s == "failed"]
    if bad:
        raise RuntimeError("; ".join(bad))
    return f"{len(rows)} synced"


def _pool(con, cfg, asof):
    kept, dead = pool.run(con, cfg["service"]["pool_symbol"], asof,
                          cfg["paths"]["generated"])
    return f"{kept} kept, {dead} dead"


def _planner(con, cfg, asof):
    def run():
        symbols = list(dict.fromkeys(
            list(cfg["run"]["symbols"])
            + extend.symbols(cfg["paths"]["extensions"])))
        tasks = planner.plan(con, symbols, asof, cfg["run"]["max_tasks"])
        gen = _gen(cfg)
        gen.cost_con = con
        gen.budget_usd = cfg["run"]["budget_usd"]
        out = planner.run(con, tasks, gen, asof)
        done = sum(1 for _t, ok in out if ok)
        return f"{done}/{len(out)} tasks"
    return run


def _report(con, cfg, asof):
    return f"-> {dashboard.build(cfg['paths']['db'], cfg['paths']['reports'])}"


register_stage("snapshot", _snapshot, order=10)
register_stage("extensions", _extensions, order=15)
register_stage("live", _live, order=20)  # plugin kinds may refresh cached data
register_stage("paper", _paper, order=30)  # same providers, so same network need
register_stage("pool", _pool, order=40, network=False)
register_stage("report", _report, order=90, network=False)


# --- commands --------------------------------------------------------------

def tick(cfg, no_network=False):
    lock = _lock(cfg["service"]["lock"])
    if lock is None:
        log.error("another tick holds %s (pid %s) -- exiting",
                  cfg["service"]["lock"], _lock_holder(cfg["service"]["lock"]))
        return 3
    try:
        logpath = _setup_logging(cfg)
        log.info("tick start (config=%s db=%s no_network=%s log=%s)",
                 cfg.get("_path"), cfg["paths"]["db"], no_network, logpath)
        con = store.connect(cfg["paths"]["db"])
        asof = store.asof_ms()
        loaded, xerrors = extend.load_active(cfg["paths"]["extensions"])
        if loaded:
            log.info("extensions: %s", ", ".join(loaded))
        for name, err in xerrors:
            log.warning("extension %s failed to load: %s", name, err)
        retries = int(cfg["service"]["retries"])
        failed = []
        for _order, name, fn, network in _STAGES:
            if no_network and network:
                runs.record(con, name, "skipped", "no-network")
                log.info("%s skipped (no-network)", name)
                continue
            ok, detail = _stage(con, name, lambda fn=fn: fn(con, cfg, asof), retries)
            if not ok:
                failed.append(f"{name}: {detail}")
        if no_network:
            runs.record(con, "planner", "skipped", "no-network")
            log.info("planner skipped (no-network)")
        else:
            ok, detail = _stage(con, "planner", _planner(con, cfg, asof),
                                retries)
            if not ok:
                failed.append(f"planner: {detail}")
        status = "ok" if not failed else "failed"
        runs.record(con, "tick", status, "; ".join(failed))
        log.info("tick %s %s", status, "; ".join(failed))
        if failed:
            notify.send(cfg, "qnl tick failed: " + "; ".join(failed))
            return 1
        return 0
    finally:
        os.close(lock)


def status(cfg):
    con = sqlite3.connect(cfg["paths"]["db"])
    asof = store.asof_ms()
    stale_ms = float(cfg["service"]["stale_hours"]) * 3_600_000
    bad = False

    print(f"db      {cfg['paths']['db']} "
          f"({os.path.getsize(cfg['paths']['db']) / 1e6:.1f} MB)")
    print(f"lock    {_lock_holder(cfg['service']['lock']) or 'free'}")
    print(f"config  {cfg.get('_path') or '(defaults)'}")

    print("\nstages (latest finished):")
    last = runs.last_status(con)
    for stage in sorted(last):
        r = last[stage]
        print(f"  {stage:<10} {r['status']:<7} {str(r['finished'])[:19]}  "
              f"{r['detail'] or ''}")
        if stage == "tick" and r["status"] != "ok":
            bad = True

    print("\nklines:")
    for sym in cfg["run"]["symbols"]:
        row = con.execute(
            "SELECT MAX(close_time) FROM klines WHERE symbol=? AND interval=?",
            (sym, "8h")).fetchone()
        close = row[0] if row else None
        if close is None:
            print(f"  {sym:<10} MISSING")
            bad = True
        else:
            age = (asof - close) / 3_600_000
            print(f"  {sym:<10} age {age:.1f}h")
            if age * 3_600_000 > stale_ms:
                bad = True

    tick_row = last.get("tick")
    if tick_row and tick_row["finished"]:
        age = (dt.datetime.now(dt.timezone.utc)
               - dt.datetime.fromisoformat(tick_row["finished"])).total_seconds()
        if age * 1000 > stale_ms:
            print(f"\nSTALE: last tick {age / 3600:.1f}h ago "
                  f"> {cfg['service']['stale_hours']}h")
            bad = True

    try:
        books = con.execute("SELECT COUNT(*) FROM live").fetchone()[0]
    except sqlite3.OperationalError:
        books = 0
    print(f"\nllm     today ${cost.today(con):.4f}, N={trials.count(con)}, "
          f"live books={books}")

    rows = paper.report(con)
    if rows:
        print("\npaper:")
        for r in rows:
            age = "never" if r["age_h"] is None else f'{r["age_h"]:.1f}h'
            print(f"  {r['book']:<22} n={r['n']:<5} eq={r['equity']:>10.2f} "
                  f"ret={r['ret']:+.2%} sr={r['sr']:+.3f} "
                  f"mdd={r['mdd']:.2%} mark {age}")
            if r["age_h"] is not None and r["age_h"] * 3_600_000 > stale_ms:
                bad = True
    return 1 if bad else 0


def doctor(cfg):
    problems = []

    def check(label, ok, note=""):
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{'  ' + note if note else ''}")
        if not ok:
            problems.append(label)

    print("config")
    path = cfg.get("_path")
    check("config file", path is None or os.path.exists(path), str(path or "(defaults)"))
    print(f"  db={cfg['paths']['db']}")
    print(f"  reports={cfg['paths']['reports']}")
    print(f"  backend={cfg['llm']['backend']} symbols={cfg['run']['symbols']}")

    print("\ndatabase")
    db = cfg["paths"]["db"]
    check("db exists", os.path.exists(db), db)
    if os.path.exists(db):
        con = sqlite3.connect(db)
        runs.connect(con)  # additive: a fresh DB has no run history yet
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("klines", "funding", "series", "trials", "live", "runs"):
            check(f"table {t}", t in tables)
        asof = store.asof_ms()
        stale_hours = float(cfg["service"]["stale_hours"])
        for sym in cfg["run"]["symbols"]:
            row = con.execute(
                "SELECT MAX(close_time) FROM klines WHERE symbol=? AND interval=?",
                (sym, "8h")).fetchone()
            close = row[0] if row else None
            age = (asof - close) / 3_600_000 if close else None
            check(f"klines {sym}", age is not None and age < stale_hours,
                  "MISSING" if age is None else f"age {age:.1f}h")

    print("\nllm key")
    key_file = cfg["llm"].get("api_key_file")
    if key_file:
        ok = os.path.exists(key_file)
        size = os.path.getsize(key_file) if ok else 0
        check("api_key_file", ok, f"{key_file} ({size} bytes)")
    else:
        print("  [--] api_key_file not configured")

    print("\ndisk")
    free = shutil.disk_usage(os.path.dirname(db) or ".").free
    check("free space", free > 1e9, f"{free / 1e9:.1f} GB")

    print("\ndeploy")
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    for unit in ("qnl-autoresearch.service", "qnl-autoresearch.timer"):
        check(unit, os.path.exists(os.path.join(unit_dir, unit)),
              os.path.join(unit_dir, unit))

    return 1 if problems else 0


def report(cfg):
    print(f"-> {dashboard.build(cfg['paths']['db'], cfg['paths']['reports'])}")
    return 0


def cli(argv=None):
    p = argparse.ArgumentParser(prog="qnl")
    p.add_argument("command", choices=["tick", "status", "doctor", "report"])
    p.add_argument("--config", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--reports", default=None)
    p.add_argument("--no-network", action="store_true")
    a = p.parse_args(argv)

    cfg = config.apply_cli(config.load(a.config), a.db, a.reports, a.no_network)
    if a.command == "tick":
        return tick(cfg, a.no_network)
    if a.command == "status":
        return status(cfg)
    if a.command == "doctor":
        return doctor(cfg)
    return report(cfg)


def main(argv=None):
    return cli(argv)


if __name__ == "__main__":
    sys.exit(cli())
