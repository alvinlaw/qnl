"""autoresearch bandit self-check: unseen origins are tried first, the best rate
wins when exploiting, and the exploration branch is reachable.
    python3 -m qnl.test_autoresearch"""
import json
import os
import tempfile

from qnl import autoresearch, codegen, scout, store, trials

_ACTS = [("github", "github:q"), ("arxiv", "arxiv:q"), ("codegen", "codegen")]


def main():
    # exploit: best gate-pass-per-slot rate wins
    got = autoresearch.choose(
        {"github:q": (10, 1), "arxiv:q": (10, 9), "codegen": (10, 5)},
        _ACTS, r=0.9)
    assert got[1] == "arxiv:q", got

    # all origins at zero gate passes: the less-tried one wins (Laplace)
    got = autoresearch.choose(
        {"github:q": (8, 0), "arxiv:q": (1, 0), "codegen": (3, 0)},
        _ACTS, r=0.9)
    assert got[1] == "arxiv:q", got

    # unseen origins are optimistic: tried before a known worse one
    got = autoresearch.choose({"arxiv:q": (10, 9), "codegen": (10, 5)},
                              _ACTS, r=0.9)
    assert got[1] == "github:q", got

    # cold start: all unseen -> some valid action
    assert autoresearch.choose({}, _ACTS, r=0.9) in _ACTS
    # exploration branch is reachable
    assert autoresearch.choose(
        {"github:q": (10, 0), "arxiv:q": (10, 0), "codegen": (10, 0)},
        _ACTS, r=0.0) in _ACTS

    # a rate-limited source is a failed slot (so the bandit learns), not a crash
    orig = scout._SOURCES["arxiv"]

    def _boom(query, n):
        raise OSError("429")

    scout._SOURCES["arxiv"] = _boom
    try:
        items, text, err = autoresearch.material("arxiv")
        assert items is None and text is None and "429" in err
    finally:
        scout._SOURCES["arxiv"] = orig

    # data arm: a proposal is appended for human approval, never fetched
    class _DataGen:
        def _chat(self, prompt, max_tokens=400):
            return ('{"metric":"etf_net_flow","host":"api.example.com",'
                    '"url":"/v1/f","cadence":"1d","pit":"settled after close"}')

    class _FamilyGen:
        def _chat(self, prompt, max_tokens=400):
            return '{"family":"x","params":[],"source":""}'

    d = os.path.join(tempfile.mkdtemp(), "g")
    old = codegen._GEN_DIR
    codegen._GEN_DIR = d
    con = store.connect(":memory:")
    try:
        assert autoresearch.propose_data(_DataGen(), con) is True
        rows = [json.loads(x) for x in open(os.path.join(d, "data_proposals.jsonl"))]
        assert rows[-1]["metric"] == "etf_net_flow" and "ts" in rows[-1]
        assert autoresearch.propose_data(_FamilyGen(), con) is False
        assert trials.reason_counts(con).get("data") == 1
    finally:
        codegen._GEN_DIR = old

    print("autoresearch self-check OK")


if __name__ == "__main__":
    main()
