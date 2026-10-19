"""Stage 4 self-check.   python3 -m qnl.test_loop"""
from qnl import benchmark, generator, loop, metrics, signals, store, trials


def main():
    # the proposer registry and the sweep registry must agree on the families
    assert set(signals.HINTS) == {n for n, _ in signals.candidates()}

    # offline generator is deterministic and starts from the given specs
    g1 = generator.OfflineGenerator(signals.candidates()[:4])
    g2 = generator.OfflineGenerator(signals.candidates()[:4])
    assert g1.propose([]) == g2.propose([])

    # the gate needs edge AND beating buy-and-hold
    assert loop.passes({"sr": 0.10, "dsr0": 0.99, "dsrb": 0.92})
    assert not loop.passes({"sr": -0.10, "dsr0": 0.99, "dsrb": 0.92})
    assert not loop.passes({"sr": 0.10, "dsr0": 0.50, "dsrb": 0.92})
    assert not loop.passes({"sr": 0.10, "dsr0": 0.99, "dsrb": 0.85})

    # LLM backend refuses to run without a key (isolation: it is only a proposer)
    try:
        generator.LLMGenerator(api_key="").propose([])
        raise AssertionError("should have raised without a key")
    except RuntimeError:
        pass

    # the shared spec parser accepts the registry and rejects anything else
    assert generator._parse_spec('{"family": "momentum", "params": [10]}') == ("momentum", (10,))
    try:
        generator._parse_spec('{"family": "nope", "params": []}')
        raise AssertionError("should have rejected an unknown family")
    except ValueError:
        pass

    # DeepSeek backend also refuses keyless (point the file at a path that can't exist)
    generator.DeepSeekGenerator._KEY_PATH = "/nonexistent/.deepseek_key"
    try:
        generator.DeepSeekGenerator(api_key="").propose([])
        raise AssertionError("should have raised without a DeepSeek key")
    except RuntimeError:
        pass

    # end-to-end: the loop records every (spec, asset) as a trial, so N grows.
    # bundle mirrors sources.load's shape; empty non-price planes are legal.
    con = store.connect(":memory:")
    assert "empty" in trials.digest(con)  # nothing tried yet
    closes = [100.0 + i for i in range(600)]
    bundle = {"price": closes, "funding": [], "basis": None, "flow": None, "opt": None}
    data = {"X": (bundle, metrics.equity_returns(benchmark.buy_and_hold(closes)))}
    hist = loop.run(con, data, generator.OfflineGenerator(signals.candidates()[:6]),
                    rounds=6, fee=0.001, asof_ms=0)
    assert len(hist) == 6
    # effective N: six sma_cross params on one asset are ONE hypothesis cell,
    # while every raw (spec, asset) row is still recorded
    assert trials.count(con) == 1
    assert con.execute("select count(*) from trials").fetchone()[0] >= 6

    # digest now names the families the loop recorded (fed to the proposer's prompt)
    d = trials.digest(con)
    assert "sma_cross" in d, d

    print("loop self-check OK")


if __name__ == "__main__":
    main()
