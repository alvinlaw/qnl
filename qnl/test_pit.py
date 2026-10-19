"""PIT invariant self-check.   python3 -m qnl.test_pit"""
from qnl import store


def _bar(sym, iv, o, c, price):
    return (sym, iv, o, c, price, price, price, price, 1.0)


def main():
    con = store.connect(":memory:")
    # daily bar: opens 0, closes 86_399_999
    store.upsert_klines(con, [_bar("BTCUSDT", "1d", 0, 86_399_999, 100.0)])
    assert store.load_asof(con, "BTCUSDT", "1d", 86_399_998) == []   # 1ms early -> invisible
    assert len(store.load_asof(con, "BTCUSDT", "1d", 86_399_999)) == 1  # at close -> visible

    # a later bar stays hidden until its own close
    store.upsert_klines(con, [_bar("BTCUSDT", "1d", 86_400_000, 172_799_999, 200.0)])
    assert len(store.load_asof(con, "BTCUSDT", "1d", 100_000_000)) == 1
    assert len(store.load_asof(con, "BTCUSDT", "1d", 172_799_999)) == 2

    print("PIT self-check OK")


if __name__ == "__main__":
    main()
