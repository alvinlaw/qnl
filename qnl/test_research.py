"""Spine self-check (sources / signals / decay).   python3 -m qnl.test_research"""
from qnl import decay, signals, sources, store


def main():
    # 1. the public registry exposes the price planes and returns a bundle
    con = store.connect(":memory:")
    b = sources.load(con, "X", 0)
    assert set(b) == {"price", "funding", "basis"} and b["price"] == [] \
        and b["basis"] is None

    # 2. a price family goes through the same contract (-> len-1 returns)
    price = [100.0 * 1.001 ** i for i in range(300)]
    r = signals.build("momentum", (10,), 0.001)({"price": price, "funding": [], "basis": None})
    assert len(r) == len(price) - 1

    # 3. decay verdict: recent half negative -> flagged; steady -> not
    assert decay.verdict([0.01, -0.005] * 30 + [-0.01, 0.005] * 30, 6)[1] is True
    assert decay.verdict([0.01, -0.005] * 60, 6)[1] is False

    # 4. the sweep covers the registered price families
    names = {n for n, _ in signals.candidates()}
    assert "sma_cross" in names, names

    print("research self-check OK")


if __name__ == "__main__":
    main()
