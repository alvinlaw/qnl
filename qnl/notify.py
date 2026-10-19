"""Optional failure alert: POST {"text": ...} to alerts.webhook_url.

No-op when unconfigured. Delivery failure is swallowed -- an alert must never
break the run. Never pass secrets in `text`; callers log stage names only.
"""
import json
import logging
import urllib.request

log = logging.getLogger("qnl")


def send(cfg, text):
    url = (cfg.get("alerts") or {}).get("webhook_url")
    if not url:
        return False
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except Exception as e:  # noqa: BLE001 -- an alert must not break the run
        log.warning("notify failed: %s: %s", type(e).__name__, e)
        return False


def _selfcheck():
    assert send({"alerts": {"webhook_url": ""}}, "x") is False
    assert send({}, "x") is False
