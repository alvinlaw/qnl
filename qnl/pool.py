"""Re-evaluate the generated family pool on the latest data and prune families
that lost their edge. Retention = the pool stays scored; promotion into
signals.FAMILIES stays manual. Re-evaluation does NOT touch the trials ledger
(N must not grow just because we looked again).

    python3 -m qnl.pool --symbol BTCUSDT
"""
import argparse
import json
import os
import shutil

from qnl import codegen, decay, sources, stats, store, trials

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")


def eval_dir(gen_dir, bundle, asof, n, var, demote_sr=0.0):
    """Re-run every pooled family on `bundle`; update its sidecar and move it to
    gen_dir/dead/ when its fresh SR <= demote_sr OR its edge is decaying. Files
    without a sidecar (saved before provenance existed) are skipped -- their
    params are unknown. -> (kept, dead).

    shortcut: scores the FULL window, not only bars after the original asof, so
    this measures edge persistence rather than clean OOS; upgrade by slicing the
    bundle at the saved asof index before check_shape."""
    try:
        names = sorted(os.listdir(gen_dir))
    except OSError:
        return 0, 0
    dead_dir = os.path.join(gen_dir, "dead")
    kept = dead = 0
    for fn in names:
        if not fn.endswith(".json"):
            continue
        name = fn[:-5]
        meta_path = os.path.join(gen_dir, fn)
        src_path = os.path.join(gen_dir, name + ".py")
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            with open(src_path) as f:
                source = f.read()
        except (OSError, ValueError):
            continue
        build, err = codegen.vet_source(source)
        rets = None
        if not err:
            rets, err = codegen.check_shape(build, meta.get("params", []), bundle)
        if err:
            meta.update(status="error", eval_error=err, eval_asof=asof)
        else:
            sr = stats.sharpe_period(rets)
            _, decaying = decay.verdict(rets)
            meta.update(status="live" if sr > demote_sr and not decaying else "dead",
                        eval_sr=sr, eval_decay=decaying, eval_asof=asof,
                        eval_dsr0=stats.dsr(rets, 0.0, n, var))
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        if meta["status"] == "dead":
            os.makedirs(dead_dir, exist_ok=True)
            shutil.move(src_path, os.path.join(dead_dir, name + ".py"))
            shutil.move(meta_path, os.path.join(dead_dir, name + ".json"))
            dead += 1
        else:
            kept += 1
    return kept, dead


def run(con, symbol, asof, gen_dir=None, demote_sr=0.0):
    bundle = sources.load(con, symbol, asof)
    n, var = trials.count(con), trials.variance(con)
    return eval_dir(gen_dir or codegen._GEN_DIR, bundle, asof, n, var, demote_sr)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    p.add_argument("--dir", default=codegen._GEN_DIR)
    p.add_argument("--demote-sr", type=float, default=0.0,
                   help="demote a family when its fresh SR is <= this (default 0)")
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    kept, dead = run(con, a.symbol, asof, a.dir, a.demote_sr)
    print(f"pool: {kept} kept, {dead} moved to dead/ -> {a.dir}")


if __name__ == "__main__":
    main()
