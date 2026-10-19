"""LLM-generated signal families, checked by a code sandbox then judged by the
SAME gate. Nothing generated is added to signals.FAMILIES -- it is evaluated in
isolation and recorded in the trials ledger like any other candidate.

An LLM writes a NEW family as source; we refuse to trust it blindly:
  1. vet_source  -- AST denylist (no imports, no dunder attrs, no open/eval/exec)
     then exec with empty __builtins__ and an explicit safe-name allowlist.
  2. check_shape -- exactly len(price)-1 finite floats, and append-invariance
     (rets[:k] must not move when later bars are appended).
  3. the trials ledger + DSR gate judges the returns, exactly as for hand-written
     families.

    python3 -m qnl.codegen --symbol BTCUSDT --backend deepseek
    python3 -m qnl.codegen --backend deepseek --n 5   # a sweep of new family ideas

Each round feeds back the directions already tried (this run + the ledger) so the
proposer does not repeat itself, and EVERY proposed family is blacklisted
(trials.swept) whether it passed or was rejected -- a reject leaves no ledger row,
so without that list a later run would re-spend a slot on the same dead idea.
Passing vet + the gate does NOT promote a family into signals.FAMILIES -- that
stays a manual step (promotion raises N for everyone).

shortcut: the sandbox is a restricted exec, NOT a hard jail -- acceptable for a
trusted operator on their own machine, NOT for untrusted code. check_shape
catches global lookahead; a subtle one-step decision peek is NOT caught here
(the DSR gate is the backstop). Upgrade to a subprocess jail + full PIT vetting
before this ever runs unsupervised.
"""
import argparse
import ast
import datetime as dt
import json
import math
import os

from qnl import critic, experience, generator, sources, stats, store, trials, walk_forward

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")

_SAFE = {"len": len, "abs": abs, "sum": sum, "min": min, "max": max,
         "range": range, "enumerate": enumerate, "zip": zip, "round": round,
         "float": float, "int": int, "sorted": sorted, "math": math,
         "any": any, "all": all}
_BAD_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals", "locals",
              "getattr", "setattr", "delattr", "vars", "dir", "input", "exit",
              "quit", "breakpoint", "type", "object", "memoryview"}
_BAD_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
              ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)

_DUP_CORR = 0.95  # |rho| above this = the same bet under a new name
_MIN_TRADES = 30  # non-zero return bars; fewer has no power under the gate

_PROMPT = '''You are writing ONE new crypto trading signal family as Python.

Write a function with EXACTLY this signature:
    def build(params, data):  -> list[float]

`params` is a list of numbers YOU choose (the family's knobs).
`data` is a dict with ONLY these keys (values are lists, oldest first):
__PLANES__

Return EXACTLY N-1 floats (one P&L per interval). PIT rule: the position at
interval i is decided from data at index <= i ONLY; it earns the move i -> i+1.
Never let a decision at i read index i+1 or later.
A plane's history can START LATER than the price history, so its early values are
None. Guard every plane read: skip bars where the value (or the whole window) is
None and emit 0.0 there. Any arithmetic on None rejects your family.
No imports. Available names ONLY: len abs sum min max range enumerate zip round
float int sorted any all math.
Give it an ECONOMIC STORY (why the edge exists), not a curve-fit.
Set every threshold in the plane's own units (a return ratio, a funding rate),
not a z-score; a threshold no bar can reach makes the family emit only 0.0 (or
trade on too few bars) and it will be rejected as inert.

Example (trailing breakout):
def build(params, data):
    p = data["price"]; w = int(params[0])
    out = []
    for i in range(len(p) - 1):
        s = 1.0 if i >= w and p[i] > p[i - w] else 0.0
        out.append(s * (p[i + 1] / p[i] - 1))
    return out

Reply ONLY JSON: {"family": "<snake_case>", "params": [<numbers>],
"source": "def build(params, data):\\n    ..."}
'''


def vet_source(source):
    """-> (build_fn, None) on success, (None, reason) on refusal. Never raises."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return None, f"syntax error: {e}"
    for node in ast.walk(tree):
        if isinstance(node, _BAD_NODES):
            return None, f"forbidden construct: {type(node).__name__}"
        if isinstance(node, ast.Name) and node.id in _BAD_NAMES:
            return None, f"forbidden name: {node.id}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return None, f"dunder attribute: {node.attr}"
    ns = {"__builtins__": {}, **_SAFE}
    try:
        exec(compile(tree, "<llm-family>", "exec"), ns)
    except Exception as e:  # noqa: BLE001 -- refuse anything that blows up at load
        return None, f"exec failed: {type(e).__name__}: {e}"
    fn = ns.get("build")
    if not callable(fn):
        return None, "no callable build(params, data)"
    return fn, None


def check_shape(fn, params, bundle):
    """-> (rets, None) if the returns are well-formed, else (None, reason)."""
    n = len(bundle["price"])
    try:
        rets = fn(list(params), bundle)
    except Exception as e:  # noqa: BLE001 -- a generated family may fail at runtime
        return None, f"call failed: {type(e).__name__}: {e}"
    if not isinstance(rets, list) or len(rets) != n - 1:
        return None, f"length {len(rets) if isinstance(rets, list) else '?'} != {n - 1}"
    for x in rets:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            return None, f"bad return value: {x!r}"
    for k in (n // 2, n - 2):  # append-invariance: a forward prefix must not move
        if k <= 0:
            continue
        try:
            pre = fn(list(params), {**bundle, "price": bundle["price"][:k + 1]})[:k]
        except Exception as e:  # noqa: BLE001
            return None, f"prefix call failed: {e}"
        if any(abs(a - b) > 1e-9 for a, b in zip(pre, rets[:k])):
            return None, f"lookahead: rets[:{k}] changed when later bars were appended"
    return rets, None


_REPAIR_PROMPT = '''The Python signal family below FAILED validation: __ERR__
Fix ONLY that failure. Keep the same economic mechanism and the signature
`def build(params, data)` returning exactly len(data["price"]) - 1 floats.
PIT rule: a decision at i may read index <= i only.
Return ONLY the corrected Python source (no JSON, no markdown fences, no
explanation).

Source:
__SOURCE__
'''


def repair_source(gen, source, err):
    """Ask the backend to fix the SAME source after a vet/shape/inert failure.
    -> corrected source or None (offline backend or unusable reply)."""
    if not hasattr(gen, "_chat"):
        return None
    try:
        text = gen._chat(
            _REPAIR_PROMPT.replace("__ERR__", err[:300])
            .replace("__SOURCE__", source[:6000]), max_tokens=1200).strip()
    except Exception:  # noqa: BLE001 -- a failed repair is just another reject
        return None
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("python"):
            text = text[6:]
    text = text.strip()
    return text if text and text != source else None


def _try_repair(gen, source, params, bundle, err, attempts=2):
    """-> ((source, fn, rets), None) if a repaired family is valid, else
    (None, last_err). Repairing the same idea is cheaper than a fresh proposal
    and keeps the one-slot/one-hypothesis contract (the name is unchanged)."""
    for _ in range(attempts):
        fixed = repair_source(gen, source, err)
        if not fixed:
            return None, err
        source = fixed
        fn, err = vet_source(source)
        if err:
            continue
        rets, err = check_shape(fn, params, bundle)
        if err:
            continue
        nz = sum(1 for x in rets if x != 0.0)
        if nz < _MIN_TRADES:
            err = f"family trades too rarely: {nz} non-zero returns"
            continue
        return (source, fn, rets), None
    return None, err


def _metas(gen_dir=None):
    """Sidecar metadata for every vetted family still in the pool (dead/ is a
    subdir, so pruned families drop out automatically)."""
    gen_dir = gen_dir or _GEN_DIR
    out = []
    try:
        names = os.listdir(gen_dir)
    except OSError:
        return out
    for fn in names:
        if fn.endswith(".json"):
            try:
                with open(os.path.join(gen_dir, fn)) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
    return out


def _pool(gen_dir=None):
    """-> [(name, params, source)] for every family still in the pool."""
    gen_dir = gen_dir or _GEN_DIR
    out = []
    for meta in _metas(gen_dir):
        try:
            with open(os.path.join(gen_dir, meta["name"] + ".py")) as f:
                out.append((meta["name"], meta.get("params", []), f.read()))
        except (OSError, KeyError):
            pass
    return out


def _pool_rets(bundle, gen_dir=None):
    """-> [(name, returns)] for the live pool, on this bundle."""
    out = []
    for name, params, source in _pool(gen_dir):
        fn, err = vet_source(source)
        if err:
            continue
        try:
            rets = fn(list(params), bundle)
        except Exception:  # noqa: BLE001 -- one broken family must not stop a sweep
            continue
        if isinstance(rets, list) and len(rets) == len(bundle["price"]) - 1:
            out.append((name, rets))
    return out


def _corr(a, b):
    """Pearson on the common tail; 0.0 when a series is too short or flat."""
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0.0 or vb <= 0.0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def _common_start(bundle):
    """First grid index where EVERY populated grid-aligned plane has data. flow/opt
    are appended to the price grid with leading None, so a plain 70% split can put
    a late plane wholly in the test half and make the train returns all zeros.
    funding/basis are shorter, unpadded series -> never trimmed. -> int

    shortcut: trims by the union of ALL plane coverage, not only the planes this
    family reads (unknown statically); price-only families get a shorter IS/OOS."""
    start = 0
    for k, v in bundle.items():
        if k in ("funding", "basis"):
            continue
        for s in (v.values() if isinstance(v, dict) else (v,)):
            if s:
                start = max(start, next((i for i, x in enumerate(s)
                                         if x is not None), len(s)))
    return start


def _trim(bundle, start):
    """Drop `start` leading bars from the grid-aligned planes only."""
    if not start:
        return bundle
    out = {}
    for k, v in bundle.items():
        if k in ("funding", "basis"):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = {m: (s[start:] if s else s) for m, s in v.items()}
        else:
            out[k] = v[start:] if v else v
    return out


def _oos(fn, params, bundle, frac=0.7):
    """Select-free OOS check on one generated family -> (is_sr, oos_sr, err).
    Splits on the window every populated plane covers (see _common_start)."""
    train, test = walk_forward.split(_trim(bundle, _common_start(bundle)), frac)
    tr, err = check_shape(fn, params, train)
    if err:
        return 0.0, 0.0, f"train: {err}"
    te, err = check_shape(fn, params, test)
    if err:
        return 0.0, 0.0, f"test: {err}"
    return stats.sharpe_period(tr), stats.sharpe_period(te), None


def outcome_digest(con, gen_dir=None, k=8):
    """Qualitative feedback for the proposer: which recent directions had edge,
    which did not, and each origin's slot -> vetted rate. Deliberately NO SR/DSR
    numbers -- the model should learn what is dead, not optimise against the gate."""
    metas = sorted(_metas(gen_dir), key=lambda m: m.get("asof") or 0)[-k:]
    lines = []
    if metas:
        def edge(m):
            # prefer the untouched holdout; fall back when it could not be scored
            if m.get("oos_err") is None and "oos_sr" in m:
                return m["oos_sr"]
            return m.get("sr", 0.0)

        pos = [m["name"] for m in metas if edge(m) > 0]
        neg = [m["name"] for m in metas if edge(m) <= 0]
        lines.append(f"Earlier vetted families (last {len(metas)}):")
        if pos:
            lines.append("  had positive edge: " + ", ".join(pos))
        if neg:
            lines.append("  had no edge: " + ", ".join(neg))
    by_origin = trials.origin_stats(con)
    if by_origin:
        lines.append("Slots spent -> vetted, by origin: " + "; ".join(
            f"{o} {ok}/{n}" for o, (n, ok) in sorted(by_origin.items())))
    rejects = trials.reason_counts(con)
    if rejects:
        lines.append("Rejects by reason: " + ", ".join(
            f"{r} {n}" for r, n in rejects.items()))
    return "\n".join(lines)


def propose(gen, avoid=(), planes=None, feedback=None, material=None,
            outcomes=None, parent=None):
    """LLM -> (family, params, source). `avoid` names families already proposed
    (this run + the ledger) so the model is steered to a NEW direction; `planes`
    lists which bundle keys hold data; `feedback` is the previous attempt's reject
    reason, fed back so a retry corrects it instead of repeating the mistake;
    `material` is external text (a paper/post) to mine -- the model may mine it,
    but must still emit a family that fits the planes above (an un-runnable idea
    is rejected by check_shape, not trusted); `parent` source turns the slot into
    a mutation of a family that already survived vetting. Uses the backend's raw
    chat channel."""
    prompt = _PROMPT.replace(
        "__PLANES__", "\n".join(
            "  " + sources.DOCS.get(k, f'"{k}"') for k in (planes or ["price"])))
    if parent:
        prompt += ("\nPARENT FAMILY (already vetted). Propose a STRUCTURALLY "
                   "DIFFERENT variant that changes ONE mechanism (the condition, "
                   "the plane it reads, or the exit rule). Keep the signature and "
                   "PIT contract. Do NOT just retune its params, and give the new "
                   "family a DIFFERENT name:\n" + parent + "\n")
    if material:
        prompt += ("\nSOURCE MATERIAL (mine it for ONE factor hypothesis with an "
                   "economic story that fits the planes above; do not copy code, "
                   "rewrite the idea):\n" + material + "\n")
    if avoid:
        prompt += ("\nThese family names were ALREADY proposed (pick a DIFFERENT "
                   "family name AND a different economic story): "
                   + ", ".join(sorted(set(avoid))))
    if outcomes:
        prompt += ("\nPAST OUTCOMES (learn which directions are already dead; "
                   "do not reuse these names, and keep exploring new directions):\n"
                   + outcomes + "\n")
    if feedback:
        prompt += (f"\nYour previous attempt was REJECTED: {feedback}\n"
                   "Fix that and reply with a corrected family.\n")
    text = gen._chat(prompt, max_tokens=1200)  # 800 truncated verbose families mid-JSON
    try:
        obj = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError as e:  # no '{'/'}' at all, or malformed JSON
        raise ValueError(f"model reply is not JSON: {text[:160]!r}") from e
    name = obj["family"]
    # the name becomes a filename: reject anything that could escape the dir
    if (not isinstance(name, str) or not 0 < len(name) <= 60
            or not all(c.isalnum() or c == "_" for c in name)):
        raise ValueError(f"unsafe family name {name!r}")
    return name, obj["params"], obj["source"]


_DATA_PROMPT = '''You are a crypto data scout. Propose ONE free, public data
source or metric that could plausibly carry trading signal and is NOT already in
this list: price, funding, basis, open interest, long/short ratio, taker flow,
liquidations, DVOL, skew, gamma exposure, put/call ratio, ETF flow.
Reply ONLY JSON:
{"metric": "<snake_case>", "host": "<api host, no key>",
 "url": "<endpoint pattern>", "cadence": "<e.g. 1h>",
 "pit": "<why a bar is point-in-time safe>"}
No credentials, no paid endpoints, no login-gated scraping.'''


def propose_data(gen):
    """LLM -> one data-source PROPOSAL (dict). Nothing is fetched: a human vets
    the endpoint before any ingester is written (auto-fetching a model-chosen URL
    is an SSRF hole)."""
    text = gen._chat(_DATA_PROMPT, max_tokens=400)
    try:
        obj = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError as e:
        raise ValueError(f"model reply is not JSON: {text[:160]!r}") from e
    for k in ("metric", "host", "url", "cadence", "pit"):
        if not isinstance(obj.get(k), str) or not obj[k].strip():
            raise ValueError(f"data proposal missing {k}")
    return {k: obj[k].strip() for k in ("metric", "host", "url", "cadence", "pit")}


def prior_names(con):
    """Family names the proposer has already spent: every swept family (pass OR
    reject) plus 'gen:' ledger rows from before the swept list existed. Feeds
    propose()'s avoid list so no slot is spent re-proposing a known idea."""
    return set(trials.swept_names(con)) | {lab.split(":", 1)[1]
                                           for lab in trials.labels(con, "gen:")}


def _stash(name, params, source, why, gen_dir=None):
    """Keep a REJECTED proposal's exact source. The ledger stores only the
    verdict; without the code a failed idea can never be re-measured."""
    d = os.path.join(gen_dir or _GEN_DIR, "attempts")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".py"), "w") as f:
        f.write(source + "\n")
    with open(os.path.join(d, name + ".json"), "w") as f:
        json.dump({"name": name, "params": params, "why": why,
                   "ts": dt.datetime.now(dt.timezone.utc).isoformat()}, f)


def sweep_families(gen, con, bundle, n, symbol, asof, material=None,
                   origin="codegen"):
    """Propose n new families, vet + shape-check each, record the passes in the
    ledger, blacklist EVERY proposed name, keep rejected source in attempts/, and
    print the gate arithmetic. Optional `material` steers the proposer; `origin`
    tags the slot for the source bandit. Nothing is promoted. -> number passed."""
    planes = [k for k in sources.SOURCES if bundle.get(k)]
    avoid = prior_names(con)  # directions spent in earlier runs
    mem = experience.format(experience.recall(
        con, " ".join([origin, (material or "")[:2000], *planes]), 6))
    digest = "\n".join(x for x in (
        outcome_digest(con), ("PAST LESSONS:\n" + mem) if mem else "") if x)
    prior = len(_metas())
    parents = _pool()
    ok = 0
    for i in range(n):
        spec = rets = None
        feedback = None
        # keep 1 slot in 3 blind so outcome feedback cannot collapse diversity
        outcomes = digest if (prior + i) % 3 != 2 else None
        # every other slot mutates a vetted family instead of inventing afresh
        parent = None
        if parents and (prior + i) % 2 == 0:
            parent = sorted(parents, key=lambda p: p[0])[(prior + i) % len(parents)][2]
        pool = _pool_rets(bundle)
        for _try in range(3):  # a bad reply or a rejected family is common; retry
            try:
                name, params, source = propose(gen, avoid, planes, feedback,
                                               material, outcomes, parent)
            except Exception as e:  # noqa: BLE001 -- a bad reply is not fatal
                feedback = f"reply is not valid JSON ({str(e)[:100]})"
                trials.reason(con, "json")
                continue
            if name in avoid:  # model ignored the avoid list -> spend the retry
                feedback = f"family name '{name}' was already proposed; pick a different one"
                trials.reason(con, "repeat")
                _stash(name, params, source, "repeat")
                continue
            trials.sweep(con, name)  # blacklist now: even a reject must not recur
            fn, err = vet_source(source)
            if err:
                trials.reason(con, "vet")
                _stash(name, params, source, "vet")
                experience.record(con, "codegen", name, params, planes,
                                  "reject", "vet", err)
            else:
                rets, err = check_shape(fn, params, bundle)
                if err:
                    trials.reason(con, "shape")
                    _stash(name, params, source, "shape")
                    experience.record(con, "codegen", name, params, planes,
                                      "reject", "shape", err)
                elif (nz := sum(1 for x in rets if x != 0.0)) < _MIN_TRADES:
                    err = (f"family trades too rarely: {nz} non-zero returns "
                           f"(need >= {_MIN_TRADES}). Widen the entry conditions "
                           "or set thresholds inside the planes' observed ranges")
                    trials.reason(con, "inert")
                    _stash(name, params, source, "inert")
                    experience.record(con, "codegen", name, params, planes,
                                      "reject", "inert", err)
            if err:  # repair the same source before spending a new proposal
                fixed, err = _try_repair(gen, source, params, bundle, err)
                if not fixed:
                    feedback = err
                    continue
                source, fn, rets = fixed
                experience.record(con, "codegen", name, params, planes,
                                  "repair", "", "repaired after validation error")
            near = max(((nm, _corr(rets, r)) for nm, r in pool),
                       key=lambda t: abs(t[1]), default=(None, 0.0))
            if abs(near[1]) > _DUP_CORR:
                feedback = (f"returns duplicate pooled family '{near[0]}' "
                            f"(corr {near[1]:+.2f}); change ONE mechanism, not params")
                trials.reason(con, "dup")
                _stash(name, params, source, "dup")
                experience.record(con, "codegen", name, params, planes,
                                  "reject", "dup", feedback)
                continue
            verdict = critic.review(gen, con, name, params, source, planes)
            if not verdict["accept"]:
                feedback = f"critic rejected: {verdict['reason']}"
                trials.reason(con, "critic")
                _stash(name, params, source, "critic")
                experience.record(con, "codegen", name, params, planes,
                                  "critic", verdict["reason"], "")
                continue
            spec = (name, params, source, fn)
            break
        if spec is None:
            trials.attempt(con, origin, False)
            print(f"[{i + 1}/{n}] gave up after 3 tries -- {feedback}")
            continue
        name, params, source, fn = spec
        avoid.add(name)
        os.makedirs(_GEN_DIR, exist_ok=True)
        path = os.path.join(_GEN_DIR, name + ".py")
        with open(path, "w") as f:
            f.write(source + "\n")
        sr = stats.sharpe_period(rets)
        trials.record(con, f"gen:{name}", symbol, sr, len(rets), asof)
        ncount, var = trials.count(con), trials.variance(con)
        p = bundle["price"]
        bh = [p[j + 1] / p[j] - 1 for j in range(len(p) - 1)]
        dsr0 = stats.dsr(rets, 0.0, ncount, var)
        dsrb = stats.dsr(rets, stats.sharpe_period(bh), ncount, var)
        gate_pass = sr > 0 and dsr0 >= 0.95 and dsrb >= 0.90
        trials.attempt(con, origin, True, gate_pass)
        if not gate_pass:
            trials.reason(con, "gate")
        experience.record(con, "codegen", name, params, planes,
                          "pass" if gate_pass else "gate",
                          "" if gate_pass else "gate rejected",
                          f"{sum(1 for x in rets if x != 0.0)} non-zero bars")
        is_sr, oos_sr, oos_err = _oos(fn, params, bundle)
        with open(os.path.join(_GEN_DIR, name + ".json"), "w") as f:
            json.dump({"name": name, "origin": origin, "symbol": symbol,
                       "asof": asof, "params": params, "sr": sr,
                       "dsr0": dsr0, "dsr_bh": dsrb, "n": ncount,
                       "gate_pass": gate_pass, "is_sr": is_sr,
                       "oos_sr": oos_sr, "oos_err": oos_err,
                       "ts": dt.datetime.now(dt.timezone.utc).isoformat()}, f)
        ok += 1
        ann = math.sqrt(1095)
        oos = "ERR" if oos_err else f"{oos_sr * ann:+.2f}"
        print(f"[{i + 1}/{n}] VETTED gen:{name} {symbol} n={len(rets)}  "
              f"SR/8h {sr:+.4f}  annSR {sr * ann:.2f}  "
              f"DSR0 {dsr0:.4f}  DSRbh {dsrb:.4f} (N={ncount})  "
              f"gate={'pass' if gate_pass else 'reject'}  "
              f"IS {is_sr * ann:+.2f}  OOS {oos} -> {path}")
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--backend", choices=["deepseek", "llm"], default="deepseek")
    p.add_argument("--n", type=int, default=1, help="how many new families to propose")
    p.add_argument("--asof", default=None)
    p.add_argument("--budget", type=float, default=2.0, help="daily LLM budget, USD")
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    gen = generator.DeepSeekGenerator() if a.backend == "deepseek" else generator.LLMGenerator()
    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    gen.cost_con = con
    gen.budget_usd = a.budget
    bundle = sources.load(con, a.symbol, asof)
    ok = sweep_families(gen, con, bundle, a.n, a.symbol, asof)
    print(f"\n{a.n} proposed, {ok} vetted + recorded. (registry unchanged -- promotion is manual)")


if __name__ == "__main__":
    main()
