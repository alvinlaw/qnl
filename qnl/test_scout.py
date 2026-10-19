"""scout self-check: the arXiv Atom parser (the network lives in fetch_arxiv).
    python3 -m qnl.test_scout"""
from qnl import scout

_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>  Funding   carry  and
      crypto  returns </title>
    <summary>
      We show that funding-rate dispersion across perps predicts
      next-period returns.
    </summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>Second</title>
    <summary>x</summary>
  </entry>
</feed>
"""


def main():
    items = scout.parse_arxiv(_ATOM)
    assert len(items) == 2, items
    # title/summary whitespace (incl. newlines) collapsed to single spaces
    assert items[0]["title"] == "Funding carry and crypto returns", items[0]["title"]
    assert items[0]["summary"] == ("We show that funding-rate dispersion across "
                                   "perps predicts next-period returns."), items[0]["summary"]
    assert items[0]["id"].endswith("/abs/2401.00001v1")

    # max_items caps the batch
    assert len(scout.parse_arxiv(_ATOM, max_items=1)) == 1
    # clip bounds each abstract (prompt stays small)
    assert len(scout.parse_arxiv(_ATOM, clip=10)[0]["summary"]) == 10

    # material joins title + summary for the proposer
    mat = scout.material_from(items)
    assert "Funding carry and crypto returns" in mat and "funding-rate" in mat

    # GitHub repo-search JSON -> repo records (default_branch may be absent)
    gh = '{"items":[{"full_name":"a/b","html_url":"https://github.com/a/b",' \
         '"description":"  funding arb  ","default_branch":"master"},' \
         '{"full_name":"c/d","html_url":"https://github.com/c/d"}]}'
    repos = scout.parse_github_repos(gh)
    assert repos[0] == {"full_name": "a/b", "branch": "master",
                        "description": "funding arb", "id": "https://github.com/a/b"}, repos[0]
    assert repos[1]["branch"] == "main"  # missing default_branch falls back
    assert len(scout.parse_github_repos(gh, max_items=1)) == 1

    print("scout self-check OK")


if __name__ == "__main__":
    main()
