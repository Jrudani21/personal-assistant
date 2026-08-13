#!/usr/bin/env python
"""KEN provider-health bot (deterministic, NO LLM).

Pings every configured LLM provider with a 1-token call and logs the result.
Alerts ONLY when a provider that was previously OK goes down (or a down one
recovers) — silent otherwise. Fits the no_agent cron pattern.

Usage:
  python qa/provider_health.py [--force]
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "provider_health.jsonl"
STATE = ROOT / "data" / "provider_health_state.json"


def _load_env() -> None:
    """Tiny .env loader (keys live in the repo .env, gitignored)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

KEYS = {
    "deepseek": os.environ.get("DEEPSEEK_API_KEY", ""),
    "openrouter": os.environ.get("OPENROUTER_API_KEY", ""),
    "sambanova": os.environ.get("SAMBANOVA_API_KEY", ""),
    "gemini": os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", ""),
    "groq": os.environ.get("GROQ_API_KEY", ""),
}

# provider -> (url, model, key_name)
PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-chat", "deepseek"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions",
                   "nvidia/nemotron-3-super-120b-a12b:free", "openrouter"),
    "sambanova": ("https://api.sambanova.ai/v1/chat/completions", "DeepSeek-V3.2", "sambanova"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "gemini-3-flash-preview", "gemini"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile", "groq"),
}


def _ping(provider: str) -> tuple[bool, str]:
    url, model, key_name = PROVIDERS[provider]
    key = KEYS.get(key_name, "")
    if not key:
        return False, "no key configured"
    import urllib.request
    import urllib.error
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()

    def _one_attempt():
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            # 429 = alive but throttled -> retry (transient); 4xx permanent -> no retry
            return e.code, f"HTTP {e.code}"
        except Exception as e:
            raise  # network error -> retry

    # Retry taxonomy: transient (429/5xx/timeouts) retried w/ backoff; 4xx never.
    from fleet_common import retry
    ok, body_out, status, _ = retry(_one_attempt, attempts=3)
    return (status in (200, 429)), body_out or f"HTTP {status}"


def main() -> int:
    now = datetime.now(timezone.utc)
    # Heartbeat: this runner IS the health-watch bot.
    try:
        from fleet_common import beat
        beat("health-watch")
    except Exception:
        pass
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    alerts = []
    results = {}
    for prov in PROVIDERS:
        ok, detail = _ping(prov)
        results[prov] = {"ok": ok, "detail": detail}
        prev = state.get(prov, {}).get("ok", None)
        if prev is not None and prev != ok:
            direction = "DOWN" if not ok else "RECOVERED"
            alerts.append(f"{direction}: {prov} ({detail})")
        elif prev is None:
            alerts.append(f"initial: {prov} {'ok' if ok else 'FAIL ' + detail}")

    # persist
    ROOT.joinpath("data").mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now.isoformat(), "results": results}) + "\n")
    STATE.write_text(json.dumps({p: {"ok": r["ok"]} for p, r in results.items()},
                                indent=2), encoding="utf-8")

    # Ollama local check (not API-keyed)
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
            ollama_ok = r.status == 200
    except Exception:
        ollama_ok = False
    results["ollama"] = {"ok": ollama_ok, "detail": "local"}

    for a in alerts:
        print(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
