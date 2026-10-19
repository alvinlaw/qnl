"""Verifier / DSR self-check.   python3 -m qnl.test_verifier"""
import sqlite3

from qnl import stats, trials


def main():
    # symmetric data -> zero skew; [1,2,3] has population kurtosis 1.5
    _, _, skew, kurt = stats._moments([1.0, 2.0, 3.0])
    assert abs(skew) < 1e-12
    assert abs(kurt - 1.5) < 1e-12

    # a strong, long, near-symmetric positive series (mean>0, ~0.33/period)
    strong = [0.01, -0.005] * 100
    assert stats.dsr(strong, 0.0, 1, 0.0) > 0.99                      # big edge, no trials
    assert stats.dsr(strong, 0.02, 1, 0.0) < stats.dsr(strong, 0.0, 1, 0.0)  # higher bar -> lower
    assert stats.dsr(strong, 0.0, 10000, 0.01) < 0.5                  # many trials kill it

    # expected max grows with trial count
    assert stats.expected_max_sr(0.01, 1) == 0.0
    assert stats.expected_max_sr(0.01, 1000) > stats.expected_max_sr(0.01, 10)

    # ledger counts distinct (label, params), not re-runs
    con = trials.connect(":memory:")
    trials.record(con, "sma", "20,50", 0.03, 100, 0)
    trials.record(con, "sma", "20,50", 0.03, 100, 0)   # same key -> no inflate
    trials.record(con, "sma", "10,30", 0.05, 100, 0)
    assert trials.count(con) == 2
    assert trials.variance(con) > 0

    # provenance: cost model is stored; a row measured by other code is flagged
    # stale but still counts as a hypothesis -- a code change does not un-evaluate
    # it, and dropping it would let a new module lower N.
    trials.record(con, "withfee", "7,9", 0.01, 100, 0, fee=0.001)
    assert con.execute("SELECT fee FROM trials WHERE label='withfee'").fetchone()[0] == 0.001
    con.execute("UPDATE trials SET code_hash='other' WHERE params='10,30'")
    assert ("sma", "10,30") in trials.stale(con)
    assert trials.count(con) == 3  # sma 20,50 + sma 10,30 (stale) + withfee
    assert "stale" in trials.audit(con) and "unknown-version" in trials.audit(con)

    # an old ledger gains the provenance columns in place (no data loss)
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE trials (label TEXT NOT NULL, params TEXT NOT NULL,"
                " sharpe REAL NOT NULL, bars INTEGER NOT NULL, asof_ms INTEGER NOT NULL,"
                " ts TEXT NOT NULL, PRIMARY KEY (label, params))")
    trials.record(old, "x", "y", 0.1, 1, 0)
    assert old.execute("SELECT code_hash FROM trials").fetchone()[0]

    print("verifier self-check OK")


if __name__ == "__main__":
    main()
