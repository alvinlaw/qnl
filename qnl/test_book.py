"""Book-level portfolio self-check (align / ENB / weights / no-lookahead).
   python3 -m qnl.test_book"""
import math

from qnl import book


def _orthogonal(n=120):
    """Three zero-mean, equal-vol, pairwise-uncorrelated streams."""
    x = [[0.01, -0.01, 0.01, -0.01],
         [0.01, 0.01, -0.01, -0.01],
         [0.01, -0.01, -0.01, 0.01]]
    return {f"s{i}": {j: row[j % 4] for j in range(n)}
            for i, row in enumerate(x)}


def main():
    streams = _orthogonal()
    names, days, rows = book.align(streams)
    assert names == ["s0", "s1", "s2"]
    assert len(days) == 120 and all(len(rows[n]) == 120 for n in names)

    # 1. uncorrelated -> 3 effective; identical -> 1
    assert abs(book.effective_n(book.corr_matrix(rows)) - 3.0) < 1e-9
    same = {n: rows["s0"] for n in ("a", "b", "c")}
    assert abs(book.effective_n(book.corr_matrix(same)) - 1.0) < 1e-9

    # 2. weights are long-only and sum to one
    for fn in (book.weights_equal, book.weights_risk_parity, book.weights_tangency):
        w = fn(rows)
        assert abs(sum(w.values()) - 1.0) < 1e-12
        assert all(v >= 0 for v in w.values())

    # 3. equal vol -> equal risk-parity weights
    w = book.weights_risk_parity(rows)
    assert max(w.values()) - min(w.values()) < 1e-9

    # 4. a single 100% stream reproduces itself
    assert book.combine({"a": rows["s0"]}, {"a": 1.0}) == rows["s0"]

    # 5. zero-vol stream is finite and gets no risk budget
    z = dict(rows)
    z["flat"] = [0.0] * 120
    wz = book.weights_risk_parity(z)
    assert wz["flat"] == 0.0 and math.isfinite(sum(wz.values()))

    # 6. no-lookahead: OOS returns on a prefix match the full run
    full = book.walk_forward(rows, window=20)
    pref = book.walk_forward({n: v[:30] for n, v in rows.items()}, window=20)
    assert len(full) == 100 and len(pref) == 10
    assert full[:10] == pref

    print("book self-check OK")


if __name__ == "__main__":
    main()
