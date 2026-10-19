"""Extension compiler tests: vetting, contracts, activation, symbols.

Deterministic -- no LLM, no network. Selfchecks build a fake api object.
"""
import os
import tempfile

from qnl import config, extend, signals, sources, store

IND = """
def positions(data, params):
    n = int(params[0]) if params else 5
    out = []
    for i in range(len(data["price"])):
        w = data["price"][max(0, i - n + 1):i + 1]
        out.append(1.0 if len(w) == n and w[-1] > sum(w) / len(w) else 0.0)
    return out
"""

IND_FUTURE = """
def positions(data, params):
    p = data["price"]
    out = []
    for i in range(len(p)):
        nxt = p[min(i + 1, len(p) - 1)]
        out.append(1.0 if nxt > p[i] else 0.0)
    return out
"""

FACTOR = """
def values(data, params):
    n = int(params[0]) if params else 3
    out = []
    for i in range(len(data["price"])):
        w = data["price"][max(0, i - n + 1):i + 1]
        out.append(w[-1] / (sum(w) / len(w)) - 1.0 if len(w) == n else None)
    return out
"""

SOURCE = """
def fetch(api, symbol, asof_ms):
    rows = api.get("api.test/series", {"s": symbol})["rows"]
    return [[t, v] for t, v in rows if t <= asof_ms]

def selfcheck():
    class _A:
        def get(self, path, params):
            return {"rows": [[0, 1.0], [1, 2.0]]}
    assert fetch(_A(), "X", 1) == [[0, 1.0], [1, 2.0]]
    assert fetch(_A(), "X", 0) == [[0, 1.0]]
    return True
"""

EXCHANGE = """
def fetch_klines(api, symbol, interval, start_ms, end_ms):
    rows = api.get("api.test/klines", {"symbol": symbol})["rows"]
    return [[symbol, interval, r[0], r[1], r[2], r[3], r[4], r[5], r[6]]
            for r in rows if start_ms <= r[1] <= end_ms]

def selfcheck():
    class _A:
        def get(self, path, params):
            return {"rows": [[0, 10, 1, 2, 0, 3, 5]]}
    got = fetch_klines(_A(), "X", "8h", 0, 10)
    assert len(got) == 1 and got[0][3] == 10, got
    return True
"""


def _m(kind, name, **kw):
    m = {"kind": kind, "name": name, "status": "active", "params": [],
         "planes": ["price"]}
    m.update(kw)
    return m


def _fake_api(rows):
    class _A:
        def get(self, path, params):
            return {"rows": rows}
    return _A()


def main():
    rules = [
        ("import os", "import"),
        ("def f():\n    return open('/etc/passwd')", "open"),
        ("def f(x):\n    return x.__class__", "dunder"),
        ("def f():\n    import os\n    return 1", "import"),
    ]
    for src, needle in rules:
        fn, err = extend.vet(src, "f")
        assert fn is None and needle in err.lower(), (needle, err)

    # indicator: contract + activation + prefix invariance
    m = _m("indicator", "test_ind", params=[5], defaults=[[5], [20]],
           hint="window int")
    ok, err = extend._contract(m, IND)
    assert ok, err
    ok, err = extend._contract(m, IND_FUTURE)
    assert not ok and "prefix" in err, err

    data = {"price": [100.0 + i + (3 if i % 7 == 0 else 0) for i in range(120)]}
    path = tempfile.mkdtemp()
    before = set(signals._DEFAULTS)
    try:
        extend._write(path, m, IND)
        signals.register("test_ind", lambda p, fee: (lambda d: [1.0] * 3),
                         lambda p: "x", "h", [])
        signals.FAMILIES.discard("test_ind")   # force the activation path
        extend._activate(m, path)
        assert "test_ind" in signals.FAMILIES
        rets = signals.build("test_ind", (5,), fee=0.0009)(data)
        assert rets and all(isinstance(r, float) for r in rets)

        # factor: contract + plane registration
        f = _m("factor", "test_fac", params=[3])
        ok, err = extend._contract(f, FACTOR)
        assert ok, err
        extend._write(path, f, FACTOR)
        extend._activate(f, path)
        assert "test_fac" in sources.SOURCES

        # source: contract + stored-plane alignment
        s = _m("source", "test_src", metric="test_metric",
               hosts=["api.test"])
        ok, err = extend._contract(s, SOURCE)
        assert ok, err
        extend._write(path, s, SOURCE)
        extend._activate(s, path)
        con = store.connect(os.path.join(path, "t.sqlite"))
        ts = [8 * 3_600_000 * (i + 1) for i in range(3)]
        store.upsert_klines(con, [("X", "8h", t - 1, t, 1, 1, 1, 1 + i, 1)
                                  for i, t in enumerate(ts)])
        store.upsert_series(con, [("X", "test_metric", ts[0], 7.0),
                                  ("X", "test_metric", ts[2], 9.0)])
        plane = sources.SOURCES["test_src"](con, "X", ts[-1])
        assert plane == [7.0, None, 9.0], plane
        con.close()

        # exchange: contract + registration
        x = _m("exchange", "test_ex", hosts=["api.test"],
               intervals=["8h"], fee=0.0005)
        ok, err = extend._contract(x, EXCHANGE)
        assert ok, err
        extend._write(path, x, EXCHANGE)
        extend._activate(x, path)
        assert "test_ex" in extend.EXCHANGES
        rows = extend.EXCHANGES["test_ex"](_fake_api([[0, 100, 1, 2, 0, 3, 5]]),
                                           "X", "8h", 0, 100)
        assert rows[0][3] == 100 and len(rows[0]) == 9, rows

        # token manifests drive the symbol list by venue support
        extend._write(path, _m("token", "test_tok", symbol="ABCUSDT",
                               venue="binance", quote="USDT"), "")
        extend._write(path, _m("token", "test_tok2", symbol="DEFUSD",
                               venue="kraken", quote="USD"), "")
        assert extend.symbols(path) == ["ABCUSDT"]
        extend._write(path, _m("exchange", "kraken", hosts=["api.kraken"],
                               intervals=["8h"]), EXCHANGE)
        assert extend.symbols(path) == ["ABCUSDT", "DEFUSD"]
        # drop earlier registrations so load_active starts from a clean registry
        sources.SOURCES.pop("test_fac", None)
        sources.SOURCES.pop("test_src", None)
        extend._FETCH.pop("test_src", None)
        extend.EXCHANGES.pop("test_ex", None)
        loaded, errors = extend.load_active(path)
        assert not errors, errors
        assert {"test_ind", "test_fac", "test_src", "kraken"} <= set(loaded)

        cfg = config.load()
        assert isinstance(cfg["paths"]["extensions"], str)
        assert os.path.isabs(cfg["paths"]["extensions"])
    finally:
        signals._DEFAULTS[:] = [d for d in signals._DEFAULTS if d in before]
        for name in ("test_ind",):
            signals.FAMILIES.discard(name)
            signals.HINTS.pop(name, None)
            signals._BUILD.pop(name, None)
            signals._FMT.pop(name, None)
        for name in ("test_fac", "test_src"):
            sources.SOURCES.pop(name, None)
            sources.DOCS.pop(name, None)
        extend._FETCH.pop("test_src", None)
        extend.EXCHANGES.pop("test_ex", None)
        extend.EXCHANGES.pop("kraken", None)


if __name__ == "__main__":
    main()
    print("ok: extend")
