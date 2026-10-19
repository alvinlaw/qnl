"""Stage 4: candidate generators.

The generator only PROPOSES. It never sees the gate — a generator optimising
against the gate is just overfitting faster.

OfflineGenerator is deterministic and always runnable. LLMGenerator (Anthropic)
and OpenAIGenerator (ChatGPT plus any OpenAI-compatible endpoint) are used only
when a key is present.
"""
import json
import os
import urllib.request

from qnl import cost, signals

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def _prompt():
    fams = "\n".join(f"  {name}({hint})" for name, hint in signals.HINTS.items())
    return (
        "Propose ONE crypto trading signal spec for a deflated-Sharpe gate.\n"
        "Families and params (ONLY these are valid):\n"
        f"{fams}\n"
        "Persistent ledger (informational, no scores -- do not optimise against it):\n"
        "{ledger}\n"
        "This run's recent proposals and their mean net-of-fee per-period Sharpe:\n{history}\n"
        "Rules:\n"
        "- Propose an idea with an economic story, not a parameter tweak of a\n"
        "  family already tried -- the gate gives grid-searching no credit.\n"
        "- Use a family different from your previous proposal.\n"
        'Reply with ONLY JSON: {{"family": "<name>", "params": [<numbers>]}}'
    )


def _parse_spec(text):
    """Model reply -> (family, params). Rejects a family outside the registry,
    so a hallucinated name can never reach the sweep."""
    obj = json.loads(text[text.index("{"): text.rindex("}") + 1])
    if obj["family"] not in signals.FAMILIES:
        raise ValueError(f"unknown family from model: {obj['family']!r}")
    return obj["family"], tuple(obj["params"])


def _read_key(path):
    """Key from a home-dir file, or None if absent (keeps the key check clean)."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


class OfflineGenerator:
    """Deterministic: probe the candidate grid, then perturb around the best."""

    _STEPS = [-5, 5, 10, -10, 20]

    def __init__(self, specs=None):
        self._queue = list(specs if specs is not None else signals.candidates())
        self._i = 0

    def propose(self, history, ledger=""):
        if self._queue:
            return self._queue.pop(0)
        name, params = max(history, key=lambda h: h["mean_sr"])["spec"]
        st = self._STEPS[self._i % len(self._STEPS)]
        self._i += 1
        p0 = params[0]
        p0 = max(1, p0 + st) if isinstance(p0, int) else round(p0 + st / 10, 2)
        return name, (p0,) + tuple(params[1:])


class _HTTPGenerator:
    """Shared propose loop; a subclass supplies _chat(prompt) -> str. Its output
    is gated exactly like the offline one — the model gets no authority."""

    cost_con = None   # set by the driver; None = do not account
    budget_usd = None

    def _check_budget(self):
        if self.cost_con is not None and self.budget_usd:
            used = cost.today(self.cost_con)
            if used >= self.budget_usd:
                raise cost.BudgetExceeded(
                    f"daily LLM budget ${self.budget_usd:.2f} reached "
                    f"(spent ${used:.4f})")

    def _spend(self, backend, usage):
        if self.cost_con is None:
            return
        cost.record(self.cost_con, backend, self._model,
                    usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0,
                    usage.get("completion_tokens",
                              usage.get("output_tokens", 0)) or 0)

    def _chat(self, prompt, max_tokens=64):
        raise NotImplementedError

    def propose(self, history, ledger=""):
        prompt = _prompt().format(ledger=ledger or "(none)",
                                  history=json.dumps(history[-20:], default=str))
        spec = self._parse_or_fallback(prompt)
        if history and spec[0] == history[-1]["spec"][0]:
            prompt += f"\nYou just proposed {spec[0]}; reply with a DIFFERENT family."
            spec = self._parse_or_fallback(prompt)
        return spec

    def _parse_or_fallback(self, prompt):
        """A reply naming a family outside the registry must not kill an unattended
        loop: fall back to the deterministic grid, rotating so it never stalls."""
        try:
            return _parse_spec(self._chat(prompt))
        except (ValueError, KeyError):
            cands = signals.candidates()
            i = getattr(self, "_fallback", 0)
            self._fallback = i + 1
            return cands[i % len(cands)]


class LLMGenerator(_HTTPGenerator):
    """Thin Anthropic adapter. Needs ANTHROPIC_API_KEY."""

    def __init__(self, model="claude-sonnet-4-5-20250929", api_key=None):
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._model = model

    def _chat(self, prompt, max_tokens=64):
        if not self._key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; use the offline backend")
        self._check_budget()
        body = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(_ANTHROPIC_URL, data=body, headers={
            "content-type": "application/json",
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            obj = json.load(r)
        self._spend("anthropic", obj.get("usage") or {})
        return obj["content"][0]["text"]


class OpenAIGenerator(_HTTPGenerator):
    """OpenAI chat adapter. Also works with any OpenAI-compatible endpoint
    (OpenRouter, Ollama, LM Studio, vLLM) via base_url; a custom endpoint may
    omit the key. Key from OPENAI_API_KEY or ~/.openai_key."""

    _ENV = "OPENAI_API_KEY"
    _KEY_PATH = os.path.expanduser("~/.openai_key")
    _DEFAULT_URL = _OPENAI_URL
    _NAME = "openai"

    def __init__(self, model="gpt-4o-mini", api_key=None, base_url=None):
        self._key = (api_key or os.environ.get(self._ENV)
                     or _read_key(self._KEY_PATH))
        self._model = model
        url = base_url or os.environ.get("OPENAI_BASE_URL") or self._DEFAULT_URL
        self._url = (url if url.endswith("/chat/completions")
                     else url.rstrip("/") + "/chat/completions")

    def _chat(self, prompt, max_tokens=64):
        if not self._key and self._url == _OPENAI_URL:
            raise RuntimeError("no OpenAI key; set OPENAI_API_KEY or ~/.openai_key")
        self._check_budget()
        body = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        req = urllib.request.Request(self._url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            obj = json.load(r)
        self._spend(self._NAME, obj.get("usage") or {})
        return obj["choices"][0]["message"]["content"]


class DeepSeekGenerator(OpenAIGenerator):
    """DeepSeek is OpenAI-compatible; same wire format, different defaults."""

    _ENV = "DEEPSEEK_API_KEY"
    _KEY_PATH = os.path.expanduser("~/.deepseek_key")
    _DEFAULT_URL = _DEEPSEEK_URL
    _NAME = "deepseek"

    def __init__(self, model="deepseek-chat", api_key=None, base_url=None):
        super().__init__(model=model, api_key=api_key,
                         base_url=base_url or self._DEFAULT_URL)

    def _chat(self, prompt, max_tokens=64):
        if not self._key:
            raise RuntimeError(
                "no DeepSeek key; set DEEPSEEK_API_KEY or ~/.deepseek_key")
        return super()._chat(prompt, max_tokens)


def create(cfg):
    """Config -> LLM generator. Backend names: anthropic/llm, openai/chatgpt,
    deepseek. `llm.model` and `llm.base_url` override the defaults."""
    llm = cfg.get("llm") or {}
    backend = (llm.get("backend") or "deepseek").strip().lower()
    key_file = llm.get("api_key_file")
    key = _read_key(key_file) if key_file else None
    model = llm.get("model") or None
    base_url = llm.get("base_url") or None
    if backend in ("llm", "anthropic", "claude"):
        return LLMGenerator(model=model or "claude-sonnet-4-5-20250929",
                            api_key=key)
    if backend in ("openai", "chatgpt", "gpt"):
        return OpenAIGenerator(model=model or "gpt-4o-mini",
                               api_key=key, base_url=base_url)
    if backend == "deepseek":
        return DeepSeekGenerator(model=model or "deepseek-chat",
                                 api_key=key, base_url=base_url)
    raise ValueError(f"unknown llm backend {backend!r}")
