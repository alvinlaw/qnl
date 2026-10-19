"""LLM-built extensions: prompt in, vetted module out.

    python3 -m qnl.extend "add PENDLE on binance and a funding z-score factor"
    python3 -m qnl.extend --list
    python3 -m qnl.extend --sync

The LLM decides names, params, code and even the fee assumption. It gets no
authority over the gate: every module is statically vetted, run against a
framework contract (shape, finite values, prefix invariance), and registered
through the same public registries. Failed attempts are quarantined with the
error, never retried automatically.

Kinds:
  token     manifest only: a symbol to ingest
  indicator positions(data, params) -> weights in [0,1]
  factor    values(data, params)    -> one value per bar
  source    fetch(api, symbol, asof_ms) -> [[time_ms, value], ...]
  exchange  fetch_klines(api, symbol, interval, start_ms, end_ms) -> 9-tuples

shortcut: generated source/exchange code runs in the same restricted exec as
qnl.codegen (no imports) with HTTP injected as `api.get`; not a hard jail.
Upgrade to a subprocess sandbox before unsupervised use.
"""
import argparse
import ast
import builtins
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from qnl import backtest, config, generator, metrics, signals, sources, store

KINDS = ("token", "indicator", "factor", "source", "exchange")
EXCHANGES = {}       # exchange name -> fetch_klines
_FETCH = {}          # source name -> fetch
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

_SAFE = {"len": len, "abs": abs, "sum": sum, "min": min, "max": max,
         "range": range, "enumerate": enumerate, "zip": zip, "round": round,
         "float": float, "int": int, "sorted": sorted, "math": math,
         "any": any, "all": all, "__build_class__": builtins.__build_class__}
_BAD_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals",
              "locals", "getattr", "setattr", "delattr", "vars", "dir",
              "input", "exit", "quit", "breakpoint", "type", "object"}
_BAD_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
              ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)


# --- vetting ---------------------------------------------------------------

def vet(source, fn_name):
    """-> (fn, None) or (None, reason). Same deny rules as qnl.codegen."""
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
    ns = {"__builtins__": dict(_SAFE), "__name__": "llm_ext"}
    try:
        exec(compile(tree, f"<llm-{fn_name}>", "exec"), ns)
    except Exception as e:  # noqa: BLE001 -- refuse anything that blows up
        return None, f"exec failed: {type(e).__name__}: {e}"
    fn = ns.get(fn_name)
    if not callable(fn):
        return None, f"no callable {fn_name}"
    return fn, None


def _bundle(planes, n=256):
    price = [100.0 * math.exp(0.2 * math.sin(i / 10.0) + 0.0003 * i)
             for i in range(n)]
    data = {"price": price}
    for p in planes:
        if p != "price":
            data[p] = [0.0001 * (math.sin(i / 7.0) + (i % 5) / 5.0)
                       for i in range(n)]
    return data


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _same(a, b):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _prefix_ok(fn, data, params):
    """Recompute on truncated data; earlier outputs must not move."""
    full = list(fn(data, list(params)))
    n = len(data["price"])
    for k in (min(40, n - 1), max(41, n // 2), n - 1):
        sub = {key: (v[:k] if isinstance(v, list) else v)
               for key, v in data.items()}
        part = list(fn(sub, list(params)))
        if len(part) != k or any(not _same(x, y)
                                 for x, y in zip(part, full[:k])):
            return f"prefix invariance failed at k={k}"
    return None


def _contract(m, source):
    """(ok, error) for one manifest+source in a synthetic setting."""
    kind, name = m["kind"], m["name"]
    if kind == "token":
        for key in ("symbol", "venue", "quote"):
            if not m.get(key):
                return False, f"token missing {key}"
        return True, None
    params = list(m.get("params") or [])
    data = _bundle(list(m.get("planes") or ["price"]))
    if kind == "indicator":
        fn, err = vet(source, "positions")
        if err:
            return False, err
        try:
            out = list(fn(data, params))
        except Exception as e:  # noqa: BLE001
            return False, f"call failed: {type(e).__name__}: {e}"
        if len(out) != len(data["price"]):
            return False, f"length {len(out)} != {len(data['price'])}"
        if any(not _finite(v) or not 0.0 <= v <= 1.0 for v in out):
            return False, "positions must be finite weights in [0,1]"
        err = _prefix_ok(fn, data, params)
        return (err is None), err
    if kind == "factor":
        fn, err = vet(source, "values")
        if err:
            return False, err
        try:
            out = list(fn(data, params))
        except Exception as e:  # noqa: BLE001
            return False, f"call failed: {type(e).__name__}: {e}"
        if len(out) != len(data["price"]):
            return False, f"length {len(out)} != {len(data['price'])}"
        if any(v is not None and not _finite(v) for v in out):
            return False, "values must be finite or None"
        err = _prefix_ok(fn, data, params)
        return (err is None), err
    if kind in ("source", "exchange"):
        fn_name = "fetch" if kind == "source" else "fetch_klines"
        fn, err = vet(source, fn_name)
        if err:
            return False, err
        check, err = vet(source, "selfcheck")
        if err:
            return False, f"missing selfcheck: {err}"
        try:
            got = check()
        except Exception as e:  # noqa: BLE001
            return False, f"selfcheck failed: {type(e).__name__}: {e}"
        if got is False:
            return False, "selfcheck returned False"
        if not m.get("hosts"):
            return False, "no allowlisted hosts"
        if kind == "source" and not m.get("metric"):
            return False, "source missing metric"
        return True, None
    return False, f"unknown kind: {kind}"


# --- registration ----------------------------------------------------------

def _stored_plane(metric):
    def plane(con, symbol, asof):
        bars = store.load_asof(con, symbol, sources.GRID, asof)
        pts = store.load_series_asof(con, symbol, metric, asof)
        if not bars or not pts:
            return None
        return sources._on_grid([p[0] for p in pts], [p[1] for p in pts],
                                [b[1] for b in bars])
    return plane


def _factor_plane(fn, params):
    def plane(con, symbol, asof):
        data = sources.load(con, symbol, asof)
        try:
            out = fn(data, list(params))
        except Exception:  # noqa: BLE001 -- one factor must not break the bundle
            return None
        if not isinstance(out, (list, tuple)) or len(out) != len(data["price"]):
            return None
        return [None if v is None else float(v) for v in out]
    return plane


def _activate(m, path):
    kind, name = m["kind"], m["name"]
    if kind == "token":
        return
    src = open(os.path.join(path, name + ".py"), encoding="utf-8").read()
    if kind == "indicator":
        fn, err = vet(src, "positions")
        if err:
            raise ValueError(err)
        params = tuple(m.get("params") or ())
        defaults = [(name, tuple(x))
                    for x in (m.get("defaults") or [m.get("params") or []])]

        def build(p, fee, fn=fn):
            p = list(p)

            def series(d):
                pos = list(fn(d, p))
                decide = lambda prefix: pos[len(prefix) - 1]  # noqa: E731
                return metrics.equity_returns(
                    backtest.run(d["price"], decide, fee=fee))
            return series

        fmt = lambda p: f"{name}({','.join(map(str, p))})"  # noqa: E731
        signals.register(name, build, fmt, m.get("hint") or name, defaults)
        return
    if kind == "factor":
        fn, err = vet(src, "values")
        if err:
            raise ValueError(err)
        if name in sources.SOURCES:
            raise ValueError(f"plane {name!r} already registered")
        sources.register_plane(name, _factor_plane(fn, m.get("params") or []),
                               m.get("doc") or name)
        return
    if kind == "source":
        fn, err = vet(src, "fetch")
        if err:
            raise ValueError(err)
        _FETCH[name] = fn
        sources.register_plane(name, _stored_plane(m["metric"]),
                               m.get("doc") or name)
        return
    if kind == "exchange":
        fn, err = vet(src, "fetch_klines")
        if err:
            raise ValueError(err)
        EXCHANGES[name] = fn
        return
    raise ValueError(f"unknown kind: {kind}")


def manifests(path):
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    out = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(path, fn), encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def load_active(path):
    """Compile+register every active manifest. -> (loaded names, errors)."""
    loaded, errors = [], []
    for m in manifests(path):
        if m.get("status") != "active":
            continue
        try:
            _activate(m, path)
            loaded.append(m["name"])
        except Exception as e:  # noqa: BLE001 -- one bad module must not stop boot
            errors.append((m.get("name", "?"), f"{type(e).__name__}: {e}"))
    return loaded, errors


def symbols(path):
    """Active token symbols whose venue has data support (binance natively,
    or an active exchange extension)."""
    venues = {m["name"] for m in manifests(path)
              if m.get("kind") == "exchange" and m.get("status") == "active"}
    out = []
    for m in manifests(path):
        if m.get("kind") != "token" or m.get("status") != "active":
            continue
        if m.get("venue") in venues | {"binance"}:
            out.append(m["symbol"])
    return list(dict.fromkeys(out))


# --- network injection for source/exchange --------------------------------

class _API:
    def __init__(self, hosts):
        self.hosts = set(hosts)

    def get(self, path, params=None):
        url = path if path.startswith("http") else "https://" + path
        host = urllib.parse.urlsplit(url).hostname
        if host not in self.hosts:
            raise PermissionError(f"host not allowlisted: {host}")
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "qnl/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(2_000_000)
        return json.loads(raw)


def _clean_series(rows, asof):
    out = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        t, v = row
        if not _finite(t) or not _finite(v) or t > asof:
            continue
        out.append((int(t), float(v)))
    return sorted(set(out))


def sync(cfg, path=None, only=None):
    """Fetch source metrics and exchange klines into the store. -> results."""
    path = path or cfg["paths"]["extensions"]
    con = store.connect(cfg["paths"]["db"])
    asof = store.asof_ms()
    ms = manifests(path)
    tokens = [m for m in ms if m.get("kind") == "token"
              and m.get("status") == "active"]
    exchanges = {m["name"]: m for m in ms if m.get("kind") == "exchange"
                 and m.get("status") == "active"}
    results = []
    for m in ms:
        if only and m["name"] not in only:
            continue
        if m.get("kind") not in ("source", "exchange") \
                or m.get("status") != "active":
            continue
        try:
            if m["kind"] == "exchange":
                fn = EXCHANGES.get(m["name"])
                if fn is None:
                    fn, err = vet(open(os.path.join(
                        path, m["name"] + ".py"), encoding="utf-8").read(),
                        "fetch_klines")
                    if err:
                        raise ValueError(err)
                api = _API(m.get("hosts") or [])
                n = 0
                for t in tokens:
                    if t.get("venue") != m["name"]:
                        continue
                    start = store.asof_ms(t.get("start"))
                    for interval in m.get("intervals") or ["8h"]:
                        last = con.execute(
                            "SELECT MAX(close_time) FROM klines"
                            " WHERE symbol=? AND interval=?",
                            (t["symbol"], interval)).fetchone()[0]
                        rows = fn(api, t["symbol"], interval,
                                  max(start, (last or 0) + 1), asof)
                        rows = [r for r in rows or []
                                if len(r) == 9 and r[3] <= asof]
                        store.upsert_klines(con, rows)
                        n += len(rows)
                        time.sleep(0.15)
                results.append((m["name"], "ok", f"{n} bars"))
            else:
                fn = _FETCH.get(m["name"])
                if fn is None:
                    fn, err = vet(open(os.path.join(
                        path, m["name"] + ".py"), encoding="utf-8").read(),
                        "fetch")
                    if err:
                        raise ValueError(err)
                api = _API(m.get("hosts") or [])
                n = 0
                for t in tokens:
                    if t.get("venue") not in exchanges | {"binance"}:
                        continue
                    rows = _clean_series(fn(api, t["symbol"], asof), asof)
                    store.upsert_series(con, [
                        (t["symbol"], m["metric"], t_, v)
                        for t_, v in rows])
                    n += len(rows)
                    time.sleep(0.15)
                results.append((m["name"], "ok", f"{n} points"))
        except Exception as e:  # noqa: BLE001 -- one source must not stop the rest
            results.append((m["name"], "failed", f"{type(e).__name__}: {e}"))
    return results


# --- LLM compiler ----------------------------------------------------------

_COMMON = """You are extending a PIT-disciplined crypto research system with ONE
Python module. No imports. Allowed names ONLY: len abs sum min max range
enumerate zip round float int sorted any all math.
PIT rule: a value at index i may only use data at indices <= i.
Existing planes: __PLANES__
Existing signal families: __FAMILIES__
Reply with ONLY a JSON object.
"""

_INDICATOR = """kind=indicator. Write:
    def positions(data, params): -> list[float]
One long-only weight per bar, same length as data["price"], each in [0.0, 1.0].
`params` is a list of numbers YOU choose. Guard None values in planes.
JSON keys: name (snake_case), params, defaults (list of at most 4 param lists),
hint (one line), doc, planes (list of plane names you read), source.
"""

_FACTOR = """kind=factor. Write:
    def values(data, params): -> list[float | None]
One value per bar, same length as data["price"]. `params` numbers YOU choose.
Return None where the plane is missing. Guard None everywhere.
JSON keys: name, params, hint, doc, planes, source.
"""

_SOURCE = """kind=source. Write:
    def fetch(api, symbol, asof_ms): -> list[[close_time_ms, float]]
    def selfcheck(): build a fake api object whose get(path, params) returns a
        small canned dict, call fetch on it, assert the rows, and return True.
Use api.get(path, params) for HTTPS; it returns parsed JSON. Never invent
future timestamps. JSON keys: name, metric (snake_case), hosts (list of
hostnames), doc, source.
"""

_EXCHANGE = """kind=exchange. Write:
    def fetch_klines(api, symbol, interval, start_ms, end_ms): -> list of 9-tuples
        (symbol, interval, open_time, close_time, open, high, low, close, volume)
    def selfcheck(): build a fake api object whose get(path, params) returns a
        small canned dict, call fetch_klines on it, assert the rows, return True.
Use api.get(path, params). Paginate if the API does. JSON keys: name (venue id),
hosts, intervals (list, e.g. ["8h"]), fee (per-side taker fee you judge
appropriate), doc, source.
"""

_TOKEN = """kind=token. Reply with JSON keys: name, symbol (venue's instrument id),
venue, quote, start (YYYY-MM-DD). No source code.
"""

_KIND_PROMPT = {"indicator": _INDICATOR, "factor": _FACTOR,
                "source": _SOURCE, "exchange": _EXCHANGE, "token": _TOKEN}

_PLAN = """Break this request into qnl extensions. Kinds: token (add a symbol),
indicator (long-only signal family), factor (derived plane), source (HTTP data
source writing a metric series), exchange (HTTP OHLCV adapter).

Request: __PROMPT__

Reply ONLY JSON: {"items": [{"kind": "...", "brief": "one sentence"}]}
At most 6 items. Skip anything that is not one of these kinds.
"""


def _json(text):
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def _gen(cfg):
    return generator.create(cfg)


def _plan(gen, prompt):
    text = gen._chat(_PLAN.replace("__PROMPT__", prompt), max_tokens=600)
    items = _json(text).get("items") or []
    return [it for it in items if it.get("kind") in KINDS][:6]


def _compile(gen, item, prompt):
    head = (_COMMON
            .replace("__PLANES__", ", ".join(sorted(sources.SOURCES)))
            .replace("__FAMILIES__", ", ".join(sorted(signals.FAMILIES))))
    text = gen._chat(head + "\n" + _KIND_PROMPT[item["kind"]]
                     + "\nRequest: " + prompt
                     + "\nThis extension: " + item.get("brief", ""),
                     max_tokens=3000)
    return _json(text)


def _repair(gen, item, prompt, obj, error):
    head = (_COMMON
            .replace("__PLANES__", ", ".join(sorted(sources.SOURCES)))
            .replace("__FAMILIES__", ", ".join(sorted(signals.FAMILIES))))
    text = gen._chat(
        head + "\n" + _KIND_PROMPT[item["kind"]]
        + "\nRequest: " + prompt
        + "\nYour previous JSON:\n" + json.dumps(obj)[:6000]
        + "\nThe framework contract rejected it: " + error
        + "\nFix it. Reply with the corrected complete JSON object only.",
        max_tokens=3000)
    return _json(text)


def _manifest(item, obj, prompt):
    m = {"kind": item["kind"], "name": obj.get("name", ""),
         "status": "active", "prompt": prompt,
         "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    for key in ("params", "defaults", "hint", "doc", "planes", "metric",
                "hosts", "intervals", "fee", "symbol", "venue", "quote",
                "start"):
        if key in obj:
            m[key] = obj[key]
    return m


def _write(path, m, source):
    os.makedirs(path, exist_ok=True)
    if source:
        with open(os.path.join(path, m["name"] + ".py"), "w",
                  encoding="utf-8") as f:
            f.write(source)
    with open(os.path.join(path, m["name"] + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(m, f, indent=2, sort_keys=True)


def add(prompt, cfg, path=None, budget=None, dry_run=False):
    """prompt -> [(name, status, detail)]. Never raises for one bad item."""
    path = path or cfg["paths"]["extensions"]
    con = store.connect(cfg["paths"]["db"])
    gen = _gen(cfg)
    gen.cost_con = con
    gen.budget_usd = budget if budget is not None else cfg["run"]["budget_usd"]
    out = []
    try:
        items = _plan(gen, prompt)
    except Exception as e:  # noqa: BLE001
        return [("(plan)", "failed", f"{type(e).__name__}: {e}")]
    for item in items:
        name = ""
        try:
            obj = _compile(gen, item, prompt)
            name = str(obj.get("name") or "").strip()
            if not _NAME_RE.match(name):
                raise ValueError(f"bad name {name!r}")
            if name in signals.FAMILIES or name in sources.SOURCES \
                    or name in EXCHANGES \
                    or os.path.exists(os.path.join(path, name + ".json")):
                raise ValueError(f"name already taken: {name}")
            source = obj.get("source") or ""
            m = _manifest(item, obj, prompt)
            ok, err = _contract(m, source)
            for _ in range(3):
                if ok:
                    break
                obj = _repair(gen, item, prompt, obj, err)
                source = obj.get("source") or ""
                m = _manifest(item, obj, prompt)
                ok, err = _contract(m, source)
            if not ok:
                m["status"] = "quarantined"
                m["error"] = err
                if not dry_run:
                    _write(path, m, source)
                out.append((name, "quarantined", err))
                continue
            if dry_run:
                out.append((name, "ok (dry-run)",
                            source.splitlines()[0] if source else "manifest"))
                continue
            _write(path, m, source)
            _activate(m, path)
            out.append((name, "ok", item.get("brief", "")))
        except Exception as e:  # noqa: BLE001 -- one item must not stop the rest
            out.append((name or item.get("kind", "?"), "failed",
                        f"{type(e).__name__}: {e}"))
    return out


# --- CLI -------------------------------------------------------------------

def cli(argv=None):
    p = argparse.ArgumentParser(prog="qnl.extend")
    p.add_argument("prompt", nargs="*")
    p.add_argument("--config", default=None)
    p.add_argument("--dir", default=None)
    p.add_argument("--budget", type=float, default=None)
    p.add_argument("--backend",
                   choices=["llm", "anthropic", "openai", "chatgpt",
                            "deepseek"], default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--sync", action="store_true")
    p.add_argument("--only", default=None, help="comma-separated names for --sync")
    a = p.parse_args(argv)

    cfg = config.load(a.config)
    if a.backend:
        cfg["llm"]["backend"] = a.backend
    path = a.dir or cfg["paths"]["extensions"]

    if a.list:
        for m in manifests(path):
            print(f"{m.get('name', '?'):<24} {m.get('kind', '?'):<10} "
                  f"{m.get('status', '?'):<12} {m.get('error', '')}")
        return 0
    if a.sync:
        only = set(a.only.split(",")) if a.only else None
        rows = sync(cfg, path, only)
        for name, status, detail in rows:
            print(f"{name:<24} {status:<8} {detail}")
        return 1 if any(s == "failed" for _n, s, _d in rows) else 0

    prompt = " ".join(a.prompt).strip()
    if not prompt:
        p.error("give a prompt, or --list / --sync")
    rows = add(prompt, cfg, path, a.budget, a.dry_run)
    for name, status, detail in rows:
        print(f"{name:<24} {status:<12} {detail}")
    return 1 if any(s in ("failed", "quarantined") for _n, s, _d in rows) else 0


if __name__ == "__main__":
    sys.exit(cli())
