"""pool self-check: a still-good family is kept, a dying one moves to dead/.
    python3 -m qnl.test_pool"""
import json
import os
import tempfile

from qnl import pool

_GOOD = '''def build(params, data):
    p = data["price"]
    return [(1.0 if i == 0 or p[i] > p[i - 1] else -1.0) * (p[i + 1] / p[i] - 1)
            for i in range(len(p) - 1)]
'''
_BAD = '''def build(params, data):
    p = data["price"]
    return [-(p[i + 1] / p[i] - 1) for i in range(len(p) - 1)]
'''
# positive overall, but the recent half's Sharpe is negative -> decaying
_DECAY = '''def build(params, data):
    p = data["price"]
    out = []
    for i in range(len(p) - 1):
        if i < 20:
            out.append(0.03 if i % 2 == 0 else 0.01)
        else:
            out.append(-0.005 if i % 2 == 0 else -0.003)
    return out
'''


def main():
    d = tempfile.mkdtemp()
    bundle = {"price": [100.0 * (1.01 ** i) for i in range(40)]}
    for name, src in (("good", _GOOD), ("bad", _BAD), ("decay", _DECAY)):
        with open(os.path.join(d, name + ".py"), "w") as f:
            f.write(src)
        with open(os.path.join(d, name + ".json"), "w") as f:
            json.dump({"name": name, "params": [], "asof": 1, "sr": 0.1}, f)

    kept, dead = pool.eval_dir(d, bundle, 2, 10, 0.01)
    assert (kept, dead) == (1, 2), (kept, dead)
    assert os.path.exists(os.path.join(d, "good.py"))
    assert os.path.exists(os.path.join(d, "dead", "bad.py"))
    assert os.path.exists(os.path.join(d, "dead", "decay.py"))
    assert not os.path.exists(os.path.join(d, "bad.py"))

    meta = json.load(open(os.path.join(d, "good.json")))
    assert meta["status"] == "live" and meta["eval_sr"] > 0, meta
    assert meta["eval_decay"] is False, meta
    dmeta = json.load(open(os.path.join(d, "dead", "decay.json")))
    assert dmeta["status"] == "dead" and dmeta["eval_decay"] is True, dmeta
    assert dmeta["eval_sr"] > 0, dmeta  # demoted by decay, not the SR floor

    print("pool self-check OK")


if __name__ == "__main__":
    main()
