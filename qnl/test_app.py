"""App self-check: a no-network cycle loads stored bars, runs the loop, writes a
report.   python3 -m qnl.test_app"""
import json
import os
import tempfile

from qnl import app, generator, signals, store

_STEP = 28_800_000  # 8h


def main():
    con = store.connect(":memory:")
    store.upsert_klines(con, [
        ("X", "8h", i * _STEP, (i + 1) * _STEP, 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1.0)
        for i in range(600)])

    with tempfile.TemporaryDirectory() as d:
        path, rep = app.cycle(con, ["X"], generator.OfflineGenerator(signals.candidates()[:3]),
                              "offline", 3, 0.001, asof=600 * _STEP + 1, report_dir=d)
        assert os.path.exists(path), path
        r = json.load(open(path))
        assert r["symbols"] == ["X"] and r["rounds"] == 3
        # three sma_cross params on one asset are one effective trial cell
        assert r["n_trials"] == 1 and len(r["proposals"]) == 3
        assert r["proposals"][0]["spec"][0] in signals.FAMILIES
        assert "accepted" in r

    # an empty store is a clean error, not a silent empty report
    empty = store.connect(":memory:")
    try:
        app.cycle(empty, ["NOPE"], generator.OfflineGenerator(), "offline", 1, 0.0, 0, ".")
        raise AssertionError("should have refused a store with no price")
    except SystemExit:
        pass

    print("app self-check OK")


if __name__ == "__main__":
    main()
