"""Configuration: defaults <- TOML file <- QNL_* env <- CLI flags.

Precedence is applied by load() for file/env; service.apply_cli() for flags.
Relative paths inside the file resolve against the file's own directory.
Secrets are never stored here -- `llm.api_key_file` is a PATH only.
"""
import os
import tomllib

_ROOT = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "paths": {
        "db": os.path.join(_ROOT, "qnl.sqlite"),
        "reports": os.path.join(_ROOT, "reports"),
        "generated": os.path.join(_ROOT, "generated"),
        "extensions": os.path.join(_ROOT, "extensions"),
    },
    "run": {
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "fee": 0.0009,
        "interval": "8h",
        "max_tasks": 5,
        "budget_usd": 2.0,
    },
    "llm": {"backend": "deepseek", "model": "", "api_key_file": "",
            "base_url": ""},
    "service": {
        "lock": "",
        "stale_hours": 26,
        "retries": 1,
        "no_network": False,
        "pool_symbol": "BTCUSDT",
    },
    "alerts": {"webhook_url": ""},
}

_ENV = {
    ("paths", "db"): ("QNL_DB", str),
    ("paths", "reports"): ("QNL_REPORTS", str),
    ("paths", "generated"): ("QNL_GENERATED", str),
    ("paths", "extensions"): ("QNL_EXTENSIONS", str),
    ("run", "symbols"): ("QNL_SYMBOLS", lambda v: [s.strip() for s in v.split(",")]),
    ("run", "fee"): ("QNL_FEE", float),
    ("run", "max_tasks"): ("QNL_MAX_TASKS", int),
    ("run", "budget_usd"): ("QNL_BUDGET_USD", float),
    ("llm", "backend"): ("QNL_BACKEND", str),
    ("llm", "model"): ("QNL_LLM_MODEL", str),
    ("llm", "base_url"): ("QNL_LLM_BASE_URL", str),
    ("llm", "api_key_file"): ("QNL_API_KEY_FILE", str),
    ("service", "no_network"): ("QNL_NO_NETWORK",
                                lambda v: v.lower() in ("1", "true", "yes")),
    ("alerts", "webhook_url"): ("QNL_WEBHOOK_URL", str),
}

_PATH_KEYS = ("db", "reports", "generated", "extensions")


def load(path=None):
    """-> nested config dict. Explicit `path` wins over $QNL_CONFIG over
    <repo>/qnl.local.toml; missing files are simply skipped."""
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    cfg["_path"] = None

    path = path or os.environ.get("QNL_CONFIG")
    if path is None:
        candidate = os.path.join(_ROOT, "qnl.local.toml")
        path = candidate if os.path.exists(candidate) else None
    if path:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        base = os.path.dirname(os.path.abspath(path))
        for section, values in raw.items():
            if section in cfg and isinstance(values, dict):
                cfg[section].update(values)
        cfg["_path"] = os.path.abspath(path)
        for key in _PATH_KEYS:
            value = cfg["paths"].get(key)
            if value and not os.path.isabs(value):
                cfg["paths"][key] = os.path.join(base, value)

    for (section, key), (env, cast) in _ENV.items():
        value = os.environ.get(env)
        if value is not None:
            cfg[section][key] = cast(value)

    for key in _PATH_KEYS:
        cfg["paths"][key] = os.path.expanduser(cfg["paths"][key])
    api_key = cfg["llm"].get("api_key_file")
    cfg["llm"]["api_key_file"] = os.path.expanduser(api_key) if api_key else ""
    if not cfg["service"].get("lock"):
        cfg["service"]["lock"] = os.path.join(cfg["paths"]["reports"], "qnl.lock")
    else:
        cfg["service"]["lock"] = os.path.expanduser(cfg["service"]["lock"])
    return cfg


def apply_cli(cfg, db=None, reports=None, no_network=None):
    """CLI flags are the last word."""
    if db:
        cfg["paths"]["db"] = os.path.expanduser(db)
    if reports:
        cfg["paths"]["reports"] = os.path.expanduser(reports)
    if no_network:
        cfg["service"]["no_network"] = True
    return cfg


def _selfcheck():
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "c.toml")
    with open(p, "w") as f:
        f.write('[paths]\ndb = "x.sqlite"\n[run]\nfee = 0.5\n')
    old = os.environ.pop("QNL_FEE", None)
    try:
        c = load(p)
        assert c["paths"]["db"] == os.path.join(d, "x.sqlite"), c["paths"]
        assert c["run"]["fee"] == 0.5
        os.environ["QNL_FEE"] = "0.25"
        assert load(p)["run"]["fee"] == 0.25
        os.environ.pop("QNL_FEE")
        assert load(p)["run"]["fee"] == 0.5
        assert apply_cli(c, db="/tmp/z.sqlite")["paths"]["db"] == "/tmp/z.sqlite"
    finally:
        os.environ.pop("QNL_FEE", None)
        if old is not None:
            os.environ["QNL_FEE"] = old


if __name__ == "__main__":
    _selfcheck()
    print("ok")
