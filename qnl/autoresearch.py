"""One unattended discovery step: a bandit picks the next place to look (GitHub,
arXiv, or free-form codegen), weighted by how many slots each origin turned into
families that PASSED THE GATE. 20% of runs explore randomly so a cold/orphaned
source is not starved. Discovery only -- promotion stays manual.

    python3 -m qnl.autoresearch --backend deepseek
"""
import argparse
import datetime as dt
import json
import os
import random

from qnl import codegen, cost, generator, scout, sources, store, trials

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_DATA_RATE = 0.1  # share of runs spent proposing a new data source


def actions():
    """Available discovery origins."""
    return [("github", "github:" + scout._DEFAULT_QUERY["github"]),
            ("arxiv", "arxiv:" + scout._DEFAULT_QUERY["arxiv"]),
            ("codegen", "codegen")]



def material(kind):
    """-> (items, text, None) or (None, None, reason). A rate-limited or dead
    external source is a failed slot, not a crashed run."""
    try:
        items = scout._SOURCES[kind](scout._DEFAULT_QUERY[kind], 6)
    except Exception as e:  # noqa: BLE001 -- the fallback below is the handling
        return None, None, f"{type(e).__name__}: {e}"
    return items, scout.material_from(items), None


def propose_data(gen, con):
    """AI proposes ONE new data source and appends it for human approval. Nothing
    is fetched: a model-chosen URL is an SSRF/credential hole until a human vets
    the endpoint and writes the ingester.

    shortcut: this arm runs on a fixed 10% budget, not bandit-scored, because its
    reward is delayed until ingestion; upgrade once ingested metrics are linked
    back to the families they produced."""
    try:
        prop = codegen.propose_data(gen)
    except Exception as e:  # noqa: BLE001 -- a bad proposal is not fatal
        trials.reason(con, "data")
        print(f"data proposal rejected: {e}")
        return False
    os.makedirs(codegen._GEN_DIR, exist_ok=True)
    path = os.path.join(codegen._GEN_DIR, "data_proposals.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps({**prop, "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
                + "\n")
    print(f"data proposal -> {path}: {prop['metric']} @ {prop['host']} "
          f"(NOT fetched -- a human approves the endpoint)")
    return True


def choose(stats, acts, r=None):
    """Epsilon-greedy: unseen origins are optimistic (tried first), otherwise the
    best gate-pass-per-slot rate wins. Laplace smoothing keeps every origin at
    zero passes distinguishable, so a less-tried one is preferred over the same
    dead origin. `r` is injectable so the test is deterministic."""
    r = random.random() if r is None else r
    if r < 0.2:
        return random.choice(acts)

    def rate(a):
        n, ok = stats.get(a[1], (0, 0))
        return (ok + 1) / (n + 2) if n else 2.0

    return max(acts, key=rate)


def step(con, symbol, gen, asof):
    """One discovery slot: bandit picks the origin, then execute it. Extracted
    so the planner can run several slots with one generator."""
    stats = trials.origin_stats(con)
    kind, origin = choose(stats, actions())
    if random.random() < _DATA_RATE:
        kind, origin = "data", "data"
    print(f"bandit -> {origin}  (stats: {stats or 'none'})")
    if kind == "data":
        return propose_data(gen, con)
    bundle = sources.load(con, symbol, asof)
    if kind == "codegen":
        ok = codegen.sweep_families(gen, con, bundle, 1, symbol, asof,
                                    origin=origin)
    else:
        items, text, err = material(kind)
        if err:
            trials.attempt(con, origin, False)  # a dead source must lose rate
            print(f"{origin} fetch failed ({err}); falling back to codegen")
            ok = codegen.sweep_families(gen, con, bundle, 1, symbol, asof,
                                        origin="codegen")
        else:
            print(f"{origin} -> {len(items)} items")
            ok = codegen.sweep_families(gen, con, bundle, 1, symbol, asof,
                                        material=text, origin=origin)
    print(f"1 slot -> {ok} vetted")
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--backend", choices=["deepseek", "llm"], default="deepseek")
    p.add_argument("--budget", type=float, default=2.0, help="daily LLM budget, USD")
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    print(trials.audit(con))
    gen = (generator.DeepSeekGenerator() if a.backend == "deepseek"
           else generator.LLMGenerator())
    gen.cost_con = con
    gen.budget_usd = a.budget
    step(con, a.symbol, gen, asof)
    print(cost.report(con))


if __name__ == "__main__":
    main()
