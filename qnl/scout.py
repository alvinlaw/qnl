"""Mine an external source for factor ideas, then run them through the SAME
vet + gate funnel as any other family -- source material is only an idea, never
trusted code or data. Nothing is promoted, and every proposed family is
blacklisted (trials.swept), so a weekly cron never re-spends a slot.

    python3 -m qnl.scout --backend deepseek --n 3
    python3 -m qnl.scout --source github --query 'funding rate arbitrage' --n 3

Sources (both keyless) register in _SOURCES as a fn(query, max_items) ->
[{"title","summary","id"}] fed into the same funnel: arXiv (Atom API) and GitHub
(repo search -> README excerpt). Another source -- Reddit's JSON API, a paper
mirror -- drops in the same way.

Run it from a scheduled job where the network is open, e.g. weekly:
    17 6 * * 1  python3 -m qnl.scout --backend deepseek --n 3 >> scout.log 2>&1
"""
import argparse
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from qnl import codegen, generator, sources, store

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_ARXIV = "http://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_arxiv(xml_text, max_items=6, clip=400):
    """Atom feed -> [{"title","summary","id"}] of the newest entries, whitespace
    collapsed and each abstract clipped so the proposer's prompt stays bounded.
    Pure (no network) so it is testable offline; fetch lives in fetch_arxiv."""
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall(f"{_ATOM}entry")[:max_items]:
        title = " ".join((e.findtext(f"{_ATOM}title") or "").split())
        summary = " ".join((e.findtext(f"{_ATOM}summary") or "").split())[:clip]
        out.append({"title": title, "summary": summary,
                    "id": (e.findtext(f"{_ATOM}id") or "").strip()})
    return out


def fetch_arxiv(query, max_items):
    url = _ARXIV + "?" + urllib.parse.urlencode({
        "search_query": query, "sortBy": "submittedDate",
        "sortOrder": "descending", "max_results": max_items})
    req = urllib.request.Request(url, headers={"User-Agent": "qnl-scout/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return parse_arxiv(r.read().decode("utf-8", "replace"), max_items)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "qnl-scout/0.1",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_github_repos(text, max_items=6):
    """GitHub repo-search JSON -> [{"full_name","branch","description","id"}].
    Pure (no network), so it is testable offline; fetch lives in fetch_github."""
    items = json.loads(text).get("items", [])[:max_items]
    return [{"full_name": it["full_name"],
             "branch": it.get("default_branch") or "main",
             "description": (it.get("description") or "").strip(),
             "id": it.get("html_url", "")} for it in items]


def fetch_github(query, max_items):
    """Keyless repo search (10 req/min unauth) -> top repos, newest first -> a
    README excerpt from each, so the model mines real strategy text rather than a
    one-line description. Repo content is an IDEA only -- never trusted code."""
    search = _get("https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": query, "sort": "updated", "per_page": max_items}))
    out = []
    for r in parse_github_repos(search, max_items):
        try:
            readme = _get(f"https://raw.githubusercontent.com/{r['full_name']}/"
                          f"{r['branch']}/README.md")
        except Exception:  # noqa: BLE001 -- a missing README must not kill the run
            readme = ""
        out.append({"title": r["full_name"],
                    "summary": " ".join((r["description"] + " " + readme).split())[:600],
                    "id": r["id"]})
    return out


# source -> fn(query, max_items) -> [{"title","summary","id"}]
_SOURCES = {"arxiv": fetch_arxiv, "github": fetch_github}
_DEFAULT_QUERY = {"arxiv": "all:cryptocurrency", "github": "cryptocurrency trading strategy"}


def material_from(items):
    return "\n\n".join(f"{it['title']}\n{it['summary']}" for it in items)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=sorted(_SOURCES), default="arxiv")
    p.add_argument("--query", default=None, help="source query (a default per source)")
    p.add_argument("--max", type=int, default=6, help="source items to mine")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--backend", choices=["deepseek", "llm"], default="deepseek")
    p.add_argument("--n", type=int, default=3, help="how many families to propose")
    p.add_argument("--asof", default=None)
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    gen = generator.DeepSeekGenerator() if a.backend == "deepseek" else generator.LLMGenerator()
    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    bundle = sources.load(con, a.symbol, asof)
    query = a.query or _DEFAULT_QUERY[a.source]
    items = _SOURCES[a.source](query, a.max)
    print(f"{a.source}:{query} -> {len(items)} items; mining {a.n} families")
    for it in items:
        print(f"  - {it['title']}")
    ok = codegen.sweep_families(gen, con, bundle, a.n, a.symbol, asof,
                                material=material_from(items),
                                origin=f"{a.source}:{query}")
    print(f"\n{a.n} proposed, {ok} vetted + recorded. (registry unchanged -- promotion is manual)")


if __name__ == "__main__":
    main()
