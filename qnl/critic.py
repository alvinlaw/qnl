"""Advisory pre-gate review for LLM-generated families.

Deterministic checks (vet/shape/inert/dup) stay authoritative. This only adds a
semantic check: is the idea new, does it have an economic story, and has it
already failed? An LLM failure or an offline backend ACCEPTS -- the gate is the
backstop, and a false block would waste a research slot.

The critic never edits the spec or source; it only votes accept/reject.
"""
import json
import sqlite3

from qnl import experience

_PROMPT = """You are a skeptical quant reviewer deciding whether ONE generated
signal family deserves a single backtest slot. You cannot know its profitability;
do NOT ask for or estimate Sharpe.

Reject ONLY when:
  (a) it is a pure parameter tweak of a family in PAST LESSONS,
  (b) it has no economic mechanism (a conditional formula with no story), or
  (c) it is the same mechanism as a past REJECTED family.
Default to ACCEPT when uncertain.

Family: __NAME__
Params: __PARAMS__
Planes available: __PLANES__
PAST LESSONS:
__LESSONS__

Source:
__SOURCE__

Reply ONLY JSON: {"accept": true/false, "reason": "<one sentence>"}
"""


def _parse(text):
    obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
    return bool(obj.get("accept", True)), str(obj.get("reason", ""))[:300]


def review(gen, con, name, params, source, planes, k=6):
    """-> {"accept": bool, "reason": str}. Never raises."""
    lessons = experience.recall(
        con, " ".join([name, str(params), *planes, source[:400]]), k)
    for r in lessons:
        if r["name"] == name and r["verdict"] in ("reject", "gate", "critic"):
            return {"accept": False,
                    "reason": f"already evaluated ({r['verdict']}): "
                              f"{r['reason'] or r['lesson']}"[:300]}
    if not hasattr(gen, "_chat"):
        return {"accept": True, "reason": "offline: deterministic checks only"}
    prompt = (_PROMPT.replace("__NAME__", name)
              .replace("__PARAMS__", str(params))
              .replace("__PLANES__", ", ".join(planes))
              .replace("__LESSONS__", experience.format(lessons) or "(none)")
              .replace("__SOURCE__", source[:3000]))
    try:
        accept, reason = _parse(gen._chat(prompt, max_tokens=300))
    except Exception as e:  # noqa: BLE001 -- advisory only; gate is the backstop
        return {"accept": True, "reason": f"critic unavailable: {type(e).__name__}"}
    return {"accept": accept, "reason": reason}


def _selfcheck():
    class G:
        def __init__(self, text):
            self.text = text

        def _chat(self, *_a, **_k):
            return self.text

    con = sqlite3.connect(":memory:")
    src = "def build(params, data):\n    return [0.0] * (len(data['price']) - 1)"
    assert review(G('{"accept": false, "reason": "pure param tweak"}'),
                  con, "x", [1], src, ["price"])["accept"] is False
    assert review(G("not json"), con, "x", [1], src, ["price"])["accept"] is True

    class Offline:
        pass

    assert review(Offline(), con, "x", [1], src, ["price"])["accept"] is True


if __name__ == "__main__":
    _selfcheck()
    print("ok")
