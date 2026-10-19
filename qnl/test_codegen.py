"""codegen self-check: the sandbox refuses unsafe code, check_shape catches
malformed returns and global lookahead.
    python3 -m qnl.test_codegen"""
import json
import os
import tempfile

from qnl import codegen, store, trials

_GOOD = '''def build(params, data):
    p = data["price"]; w = int(params[0])
    out = []
    for i in range(len(p) - 1):
        s = 1.0 if i >= w and p[i] > p[i - w] else 0.0
        out.append(s * (p[i + 1] / p[i] - 1))
    return out
'''

_BUNDLE = {"price": [100.0 + i for i in range(50)],
           "funding": [], "basis": None, "flow": None, "opt": None}


def main():
    fn, err = codegen.vet_source(_GOOD)
    assert err is None and callable(fn), err
    rets, err = codegen.check_shape(fn, [5], _BUNDLE)
    assert err is None and len(rets) == 49, err

    # unsafe sources are refused before any exec
    for src in ("import os\ndef build(params, data):\n    return []",
                'def build(params, data):\n    return open("/etc/passwd").read()',
                "def build(params, data):\n    return (1).__class__"):
        fn, err = codegen.vet_source(src)
        assert fn is None and err, (src, err)

    # malformed returns are caught: length, non-finite, and global lookahead
    cases = [
        ("def build(params, data):\n    return [0.0]", "length"),
        ('def build(params, data):\n    return [float("nan")] * (len(data["price"]) - 1)', "nan"),
        ('def build(params, data):\n    p = data["price"]\n    m = sum(p) / len(p)\n'
         "    return [p[i] / m - 1 for i in range(len(p) - 1)]", "lookahead"),
    ]
    for src, why in cases:
        fn, err = codegen.vet_source(src)
        assert fn and not err, (src, err)
        rets, err = codegen.check_shape(fn, [], _BUNDLE)
        assert rets is None and err, (why, err)

    # propose() steers the model away from directions already tried
    seen = {}

    class _Gen:
        def _chat(self, prompt, max_tokens=64):
            seen["p"] = prompt
            return '{"family":"brand_new","params":[3],"source":"def build(p,d):\\n    return []"}'

    name, params, source = codegen.propose(_Gen(), ["alpha_flag", "beta_flag"])
    assert name == "brand_new" and "alpha_flag" in seen["p"] and "beta_flag" in seen["p"]
    assert "ALREADY proposed" in seen["p"]

    # a rejected attempt's reason is fed back so the retry corrects it
    codegen.propose(_Gen(), [], ["price"], feedback="lookahead in rets[:10]")
    assert "REJECTED" in seen["p"] and "lookahead in rets[:10]" in seen["p"]

    # the family name becomes a filename, so a path-like name is refused
    class _Evil:
        def _chat(self, prompt, max_tokens=64):
            return ('{"family":"../x","params":[],"source":'
                    '"def build(p,d):\\n    return []"}')

    try:
        codegen.propose(_Evil(), [])
        raise AssertionError("should have rejected a path-like family name")
    except ValueError:
        pass
    # the prompt warns plane history can start late (leading None values)
    assert "None" in codegen._PROMPT and "0.0" in codegen._PROMPT

    # mutation slots hand the model a vetted parent, not just a blank page
    codegen.propose(_Gen(), [], ["price"],
                    parent="def build(params, data):\n    return []")
    assert "PARENT FAMILY" in seen["p"] and "return []" in seen["p"]

    # data-source arm: a structured proposal is parsed, not executed or fetched
    class _DataGen:
        def _chat(self, prompt, max_tokens=64):
            return ('{"metric":"etf_net_flow","host":"api.example.com",'
                    '"url":"/v1/etf/flow","cadence":"1d","pit":"settled after close"}')

    prop = codegen.propose_data(_DataGen())
    assert prop["metric"] == "etf_net_flow" and prop["host"] == "api.example.com"
    try:
        codegen.propose_data(_Gen())  # family JSON lacks the required keys
        raise AssertionError("should have rejected an incomplete data proposal")
    except ValueError:
        pass

    # prior_names() reads spent families back from the ledger (across runs)
    con = store.connect(":memory:")
    assert codegen.prior_names(con) == set()
    trials.record(con, "gen:old_idea", "X", 0.1, 10, 1)
    trials.record(con, "carry(-9.0,-9.0,30)", "X", 0.0, 10, 1)  # non-gen row ignored
    assert codegen.prior_names(con) == {"old_idea"}
    # pairs() exposes (label, params) cells so a driver skips only done cells
    assert ("gen:old_idea", "X") in trials.pairs(con)
    assert ("carry(-9.0,-9.0,30)", "X") in trials.pairs(con)

    # a swept family (vet/shape reject -> no trial row) is also never re-proposed
    trials.sweep(con, "vet_rejected_idea")
    trials.sweep(con, "vet_rejected_idea")  # idempotent
    assert trials.swept_names(con) == ["vet_rejected_idea"]
    assert codegen.prior_names(con) == {"old_idea", "vet_rejected_idea"}

    # a name the model was told to avoid is refused and the retry re-proposes,
    # so a repeat never re-spends a slot or writes a duplicate generated file
    class _Repeat:
        def __init__(self):
            self.calls = 0

        def _chat(self, prompt, max_tokens=1024):
            self.calls += 1
            fam = "old_idea" if self.calls == 1 else "fresh_idea"
            return ('{"family":"%s","params":[3],"source":"def build(p,d):\\n'
                    '    return [0.001] * (len(d[\\"price\\"]) - 1)"}' % fam)

    gen_dir = os.path.join(tempfile.mkdtemp(), "g")
    old_dir = codegen._GEN_DIR
    codegen._GEN_DIR = gen_dir
    try:
        assert codegen.sweep_families(_Repeat(), con, _BUNDLE, 1, "X", 1) == 1
    finally:
        codegen._GEN_DIR = old_dir
    assert ("gen:fresh_idea", "X") in trials.pairs(con)
    assert sum(1 for lab, _ in trials.pairs(con) if lab == "gen:old_idea") == 1

    # retention: the sidecar carries provenance + both gates' inputs + holdout
    meta = json.load(open(os.path.join(gen_dir, "fresh_idea.json")))
    assert meta["origin"] == "codegen" and meta["params"] == [3], meta
    assert "dsr0" in meta and "dsr_bh" in meta, meta
    assert "is_sr" in meta and "oos_sr" in meta and "gate_pass" in meta, meta
    # the slot is logged for the source bandit: 1 slot, 0 gate passes
    assert trials.origin_stats(con) == {"codegen": (1, 0)}, trials.origin_stats(con)
    # failures are counted by reason (old_idea was a repeat; fresh_idea missed the gate)
    assert trials.reason_counts(con).get("repeat") == 1, trials.reason_counts(con)
    # the digest is qualitative only -- no raw SR/DSR reaches the proposer
    digest = codegen.outcome_digest(con, gen_dir)
    assert "0.0" not in digest and "Rejects by reason" in digest, digest

    # a rejected proposal keeps its source, so the idea can be re-measured
    class _Bad:
        def _chat(self, prompt, max_tokens=1200):
            return ('{"family":"unsafe_idea","params":[],"source":'
                    '"import os\\ndef build(params, data):\\n    return []"}')

    gen_dir3 = os.path.join(tempfile.mkdtemp(), "g3")
    codegen._GEN_DIR = gen_dir3
    try:
        assert codegen.sweep_families(_Bad(), con, _BUNDLE, 1, "X", 1) == 0
    finally:
        codegen._GEN_DIR = old_dir
    ap = os.path.join(gen_dir3, "attempts", "unsafe_idea.json")
    assert json.load(open(ap))["why"] == "vet", json.load(open(ap))
    assert os.path.exists(os.path.join(gen_dir3, "attempts", "unsafe_idea.py"))

    # a family that never trades is fed back and retried, never recorded as a trial
    class _Inert:
        def _chat(self, prompt, max_tokens=1200):
            return ('{"family":"flat_idea","params":[],"source":'
                    '"def build(p,d):\\n    return [0.0] * (len(d[\\"price\\"]) - 1)"}')

    gen_dir4 = os.path.join(tempfile.mkdtemp(), "g4")
    codegen._GEN_DIR = gen_dir4
    try:
        assert codegen.sweep_families(_Inert(), con, _BUNDLE, 1, "X", 1) == 0
    finally:
        codegen._GEN_DIR = old_dir
    assert ("gen:flat_idea", "X") not in trials.pairs(con)
    assert trials.reason_counts(con).get("inert") == 3, trials.reason_counts(con)
    im = json.load(open(os.path.join(gen_dir4, "attempts", "flat_idea.json")))
    assert im["why"] == "inert", im

    # ... and so is a family that trades on too few bars to have any power
    class _Sparse:
        def _chat(self, prompt, max_tokens=1200):
            return ('{"family":"sparse_idea","params":[],"source":'
                    '"def build(p,d):\\n    return [0.001 if j < 5 else 0.0 '
                    'for j in range(len(d[\\"price\\"]) - 1)]"}')

    codegen._GEN_DIR = gen_dir4
    try:
        assert codegen.sweep_families(_Sparse(), con, _BUNDLE, 1, "X", 1) == 0
    finally:
        codegen._GEN_DIR = old_dir
    assert trials.reason_counts(con).get("inert") == 6, trials.reason_counts(con)

    # a plane that starts after the 70% cut still yields a real IS sample: the
    # holdout is split on the window every populated plane covers
    _LATE = '''def build(params, data):
    p = data["price"]; f = data["flow"]["x"]
    return [(1.0 if f[i] is not None and f[i] > 0 else 0.0) * (p[i + 1] / p[i] - 1)
            for i in range(len(p) - 1)]
'''
    px = [100.0]
    for i in range(99):
        px.append(px[-1] * (1.02 if i % 2 == 0 else 1.01))
    late = {"price": px, "funding": [], "basis": None,
            "flow": {"x": [None] * 75 + [1.0] * 25}, "opt": None}
    fn, err = codegen.vet_source(_LATE)
    assert err is None, err
    is_sr, oos_sr, err = codegen._oos(fn, [], late)
    assert err is None and is_sr > 0.0 and oos_sr > 0.0, (is_sr, oos_sr, err)

    # correlation dedup: an identical mechanism is |rho| == 1; a flat one is 0
    d2 = os.path.join(tempfile.mkdtemp(), "pool")
    os.makedirs(d2)
    with open(os.path.join(d2, "mom.py"), "w") as f:
        f.write(_GOOD)
    with open(os.path.join(d2, "mom.json"), "w") as f:
        json.dump({"name": "mom", "params": [5]}, f)
    pool = codegen._pool_rets(_BUNDLE, d2)
    fn, _ = codegen.vet_source(_GOOD)
    mom, err = codegen.check_shape(fn, [5], _BUNDLE)
    assert err is None and len(pool) == 1, (err, pool)
    assert abs(codegen._corr(pool[0][1], mom) - 1.0) < 1e-12
    assert abs(codegen._corr(pool[0][1], mom)) > codegen._DUP_CORR
    assert codegen._corr([0.0] * 49, mom) == 0.0

    print("codegen self-check OK")


if __name__ == "__main__":
    main()
