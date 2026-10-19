"""Funding ingest self-check.   python3 -m qnl.test_funding"""
from qnl import funding, store


def main():
    # 1. Binance response -> store rows
    raw = [{"fundingTime": 1000, "fundingRate": "0.00010000"},
           {"fundingTime": 2000, "fundingRate": "-0.00025000"}]
    assert funding.to_rows("BTCUSDT", raw) == [("BTCUSDT", 1000, 1e-4),
                                              ("BTCUSDT", 2000, -2.5e-4)]

    # 2. store PIT gate: a rate settled after asof is invisible
    con = store.connect(":memory:")
    store.upsert_funding(con, [("BTCUSDT", 1000, 1e-4), ("BTCUSDT", 2000, 2e-4)])
    assert store.load_funding_asof(con, "BTCUSDT", 1500) == [(1000, 1e-4)]
    assert store.load_funding_asof(con, "BTCUSDT", 2000) == [(1000, 1e-4), (2000, 2e-4)]

    # 3. basis aligns to the bar closed at/before each funding time; None if none yet
    spot = [(0, 99, 0, 0, 0, 100.0, 0), (0, 199, 0, 0, 0, 110.0, 0)]
    mark = [(0, 99, 0, 0, 0, 101.0, 0), (0, 199, 0, 0, 0, 112.2, 0)]
    b = funding.basis_series([10, 100, 200], spot, mark)
    assert b[0] is None and abs(b[1] - 0.01) < 1e-12 and abs(b[2] - 0.02) < 1e-12, b

    print("funding self-check OK")


if __name__ == "__main__":
    main()
