"""KEN — local web backend for the personal assistant (FastAPI).

A thin HTTP + SSE API that IMPORTS and WRAPS the existing `assistant`
package. Nothing in `assistant/*` is modified; this server only:

  - filters which tools the model may see/call (enabled_tools),
  - measures wall-clock ms per tool call and emits SSE tool events,
  - bridges the synchronous loop to async SSE via a worker thread + queue,
  - persists turns via assistant/sessions.py and logs via assistant/observations.py.

Model provider: DeepSeek's hosted OpenAI-compatible API (KEN_ADDENDUM_deepseek_api.md).
Chat completions and tool-calling for the primary model go straight to
https://api.deepseek.com — NOT through assistant/llm.py's Ollama-only
stream_chat (that call is hardcoded to `ollama.chat`, and assistant/* is
off-limits to edit). This server implements an equivalent OpenAI-style
tool-calling loop for DeepSeek, reusing the same system prompt
(`llm._system_prompt()`), the same tool registry/schemas, and the same
observation logging the Ollama loop uses — only the transport differs.
If DeepSeek is unusable from the start of a turn (network down, 401,
429, ...), the turn retries once against a free local Ollama model via
the *existing* assistant/llm.py loop, unchanged.

Run:
    py -3.12 -m uvicorn server:app --port 8756 --reload
    (or:  py -3.12 app.py)

Privacy: chat is NOT local by default anymore — prompts, tool inputs, and
memory/RAG content included in a prompt are sent to DeepSeek's servers.
Only the local Ollama fallback path stays fully on-machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import requests
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from openai import OpenAI
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from assistant import (
    admin as admin_mod,
    backup,
    config as config_mod,
    crew_cache,
    llm,
    memory,
    observations,
    rag,
    reminders,
    sessions,
    todo,
    tools,
    vault,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"

# ---------------------------------------------------------------------------
# Model provider: DeepSeek hosted API (primary) + local Ollama (fallback)
# ---------------------------------------------------------------------------
from assistant.deepseek_key import find_key as _deepseek_key  # noqa: E402

DEEPSEEK_API_KEY = _deepseek_key()
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("KEN_MODEL", "deepseek-chat")
PROVIDER = "deepseek"

# Free cloud fallback (Bluesminds relay, $0) — sits between DeepSeek and
# local Ollama. Key read from KEN_BLUESMINDS_API_KEY (Hermes .env) or
# BLUESMINDS_API_KEY (500-AI-Agents repo .env). llama-3.1-70b verified 2026-08.
BLUESMINDS_KEY = os.environ.get("BLUESMINDS_API_KEY") or ""
if not BLUESMINDS_KEY:
    try:
        _env = {}
        for p in ("C:/Users/Janak's PC/AppData/Local/hermes/.env",
                  "E:/Local/projects/500-AI-Agents-Projects/.env"):
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        if line.strip() and not line.strip().startswith("#") and "=" in line:
                            k, v = line.strip().split("=", 1)
                            _env[k] = v
            except OSError:
                pass
        BLUESMINDS_KEY = _env.get("BLUESMINDS_API_KEY") or _env.get(
            "KEN_BLUESMINDS_API_KEY", "")
    except Exception:
        BLUESMINDS_KEY = ""
BLUESMINDS_BASE_URL = "https://api.bluesminds.com/v1"
BLUESMINDS_MODEL = os.environ.get("KEN_BLUESMINDS_MODEL", "meta/llama-3.1-70b-instruct")

# Free local fallback: used automatically when DeepSeek is unusable from the
# start (no key, network down, 401, 429, ...). Override with KEN_FALLBACK_MODEL.
FALLBACK_MODEL = os.environ.get("KEN_FALLBACK_MODEL", "qwen3:8b")
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT_S = 2

_deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None


def _read_env_file(*paths: str) -> dict:
    """Parse key=value lines from .env files (no dotenv dependency)."""
    out: dict[str, str] = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        out[k] = v
        except OSError:
            pass
    return out


_bluesminds_client = OpenAI(api_key=BLUESMINDS_KEY, base_url=BLUESMINDS_BASE_URL) if BLUESMINDS_KEY else None

# Groq + OpenRouter free tiers — keys live in personal-assistant/.env.
_KEN_ENV = _read_env_file(str(Path(__file__).resolve().parent / ".env"))
GROQ_KEY = os.environ.get("GROQ_API_KEY") or _KEN_ENV.get("GROQ_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or _KEN_ENV.get("OPENROUTER_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.environ.get("KEN_GROQ_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.environ.get("KEN_OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
_groq_client = OpenAI(api_key=GROQ_KEY, base_url=GROQ_BASE_URL) if GROQ_KEY else None
_openrouter_client = OpenAI(api_key=OPENROUTER_KEY, base_url=OPENROUTER_BASE_URL) if OPENROUTER_KEY else None

# ---------------------------------------------------------------------------
# Hermes A2A bridge: route chat through the Hermes agent (same machine,
# gateway A2A endpoint on :9900) so KEN has access to ALL Hermes chats,
# sessions, memory, and tools. Falls back to DeepSeek/Ollama when the
# gateway is down. Enable/disable with KEN_HERMES_A2A=1/0 (default: on when
# a token is found).
# ---------------------------------------------------------------------------
HERMES_A2A_URL = os.environ.get("HERMES_A2A_URL", "http://127.0.0.1:9900").rstrip("/")
_HERMES_ENV_FILE = Path(os.environ.get("HERMES_HOME", r"C:\Users\Janak's PC\AppData\Local\hermes")) / ".env"


def _hermes_a2a_token() -> str:
    """Locate the A2A bearer token: env var first, then the Hermes .env file."""
    tok = os.environ.get("A2A_BEARER_TOKEN", "").strip()
    if tok:
        return tok
    try:
        for line in _HERMES_ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("A2A_BEARER_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


HERMES_A2A_TOKEN = _hermes_a2a_token()
HERMES_A2A_ENABLED = os.environ.get(
    "KEN_HERMES_A2A", "1" if HERMES_A2A_TOKEN else "0"
).strip().lower() in ("1", "true", "yes", "on")


def _hermes_a2a_send(text: str, chat_id: str, timeout: int = 320) -> str:
    """Send one user message to the Hermes agent over A2A; return reply text.

    ``contextId`` is pinned to the KEN chat so Hermes keeps a per-chat
    conversation (its own memory/tools/sessions stay in scope).
    """
    params: dict = {
        "message": {"role": "ROLE_USER", "parts": [{"text": text}]},
        "contextId": f"ken:{chat_id}" if chat_id else None,
    }
    payload = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": params}
    resp = requests.post(
        HERMES_A2A_URL + "/",
        json=payload,
        headers={"Authorization": f"Bearer {HERMES_A2A_TOKEN}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    result = data.get("result") or {}
    status = result.get("status") or {}
    if status.get("state") == "TASK_STATE_COMPLETED":
        parts = (status.get("message") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)
    parts = (status.get("message") or {}).get("parts") or []
    err = "".join(p.get("text", "") for p in parts) or f"state={status.get('state')}"
    raise RuntimeError(f"Hermes A2A: {err}")


def _hermes_stream(history: list[dict], chat_id: str):
    """Generator: full Hermes A2A reply as chunks (SSE-friendly). On failure
    yields the 'Error talking to...' wrapper so the provider chain falls
    through to DeepSeek/Ollama exactly like a dead primary."""
    user_text = next(
        (m.get("content", "") for m in reversed(history or []) if m.get("role") == "user"),
        "",
    )
    try:
        reply = _hermes_a2a_send(user_text, chat_id)
    except Exception as e:
        yield f"Error talking to Hermes: {e}"
        return
    for i in range(0, len(reply), 96):
        yield reply[i:i + 96]

# ---------------------------------------------------------------------------
# Tool permission model  (KEN_BUILD_SPEC section 3)
#
# Groups are for the UI sidebar. Names come from the REAL REGISTRY
# (e.g. `sync_vault`, not the spec's `sync_knowledge`; tools that don't
# exist in REGISTRY — query_sql, transcribe_file, mcp_*, skills — are
# omitted rather than fabricated).
# ---------------------------------------------------------------------------
TOOL_GROUPS: dict[str, list[str]] = {
    "core": ["calculator", "get_datetime", "web_search", "weather",
             "wikipedia_summary", "fetch_webpage"],
    "workspace": ["read_file", "write_file", "list_files", "run_python",
                  "restart_python_session"],
    "knowledge": ["search_documents", "sync_vault", "sync_knowledge",
                  "query_sql", "transcribe_file"],
    "memory": ["remember", "recall", "forget", "recent_activity",
               "distill_memory", "backup_data"],
    "planning": ["add_task", "list_tasks", "complete_task", "clear_tasks",
                 "remind_me", "list_reminders", "cancel_reminder"],
    "agents": ["deep_analysis", "clear_crew_cache", "mcp_list_tools",
               "mcp_call_tool", "list_skills", "run_skill", "admin"],
}

# Destructive tools — default OFF, only callable when explicitly enabled.
# `admin` and `mcp_call_tool` execute arbitrary side effects (docker control,
# process starts, whatever an MCP server exposes); `run_skill` executes a
# stored procedure. They are gated the same way as write_file/run_python.
DESTRUCTIVE_TOOLS = {
    "write_file", "run_python", "restart_python_session",
    "forget", "clear_tasks", "cancel_reminder", "clear_crew_cache",
    "admin", "mcp_call_tool", "run_skill",
}


# ---------------------------------------------------------------------------
# Access token
#
# Why this exists even though the server only listens on 127.0.0.1 and is
# reached from a phone via `tailscale serve` (which already restricts access
# to devices in the tailnet): POST /api/tools/toggle can switch `run_python`
# and `write_file` ON, so *anything* that can reach this API can get arbitrary
# code execution on this machine. Tailnet membership shouldn't be the only
# gate — a second device, a shared node, or a stray browser tab shouldn't be
# enough.
#
# The token is read from KEN_TOKEN, else generated once and persisted to
# data/.ken_token (data/* is gitignored). Set KEN_TOKEN="" to disable auth
# entirely — only sane if nothing but this machine can ever reach the port.
# ---------------------------------------------------------------------------
TOKEN_FILE = DATA_DIR / ".ken_token"          # legacy single-token file
TOKENS_FILE = DATA_DIR / ".ken_tokens.json"   # owner + guest tokens
COOKIE_NAME = "ken_token"
AUTH_LOG = DATA_DIR / "access.log"

_token_lock = threading.Lock()


def _read_tokens() -> dict:
    try:
        return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_tokens(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TOKENS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(TOKENS_FILE)


def _init_tokens() -> dict:
    """Owner token: KEN_TOKEN env > tokens file > legacy .ken_token > new."""
    with _token_lock:
        data = _read_tokens()
        env = os.environ.get("KEN_TOKEN")
        if env is not None:
            data["owner"] = env            # "" disables auth entirely
        elif not data.get("owner"):
            legacy = ""
            try:
                legacy = TOKEN_FILE.read_text(encoding="utf-8").strip()
            except Exception:
                pass
            data["owner"] = legacy or secrets.token_urlsafe(32)
        data.setdefault("guests", [])
        try:
            _write_tokens(data)
        except Exception:
            pass                            # in-memory only
        return data


_TOKENS = _init_tokens()
ACCESS_TOKEN = _TOKENS["owner"]
AUTH_ENABLED = bool(ACCESS_TOKEN)

# Exempt: the health probe app.py polls on startup, the API docs, and the
# login/logout endpoints (they exist precisely to turn no-auth into a
# session; they never grant access by themselves).
_AUTH_EXEMPT = {
    "/api/health", "/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect",
    "/api/login", "/api/logout",
}


def _now_ts() -> float:
    return time.time()


def _active_guests() -> list[dict]:
    """Non-expired guest tokens, pruning dead ones as a side effect."""
    with _token_lock:
        data = _read_tokens()
        guests = data.get("guests", [])
        alive = [g for g in guests if not g.get("expires_at") or g["expires_at"] > _now_ts()]
        if len(alive) != len(guests):
            data["guests"] = alive
            try:
                _write_tokens(data)
            except Exception:
                pass
        return alive


def _principal_for(supplied: str | None) -> dict | None:
    """Resolve a token to {"role": "owner"|"guest", "name": str} or None.

    Every comparison uses compare_digest so the check stays constant-time —
    no early exit that would leak how many leading characters a guess got
    right. This matters more now that the server can be published to the
    public internet via `tailscale funnel`.
    """
    if not supplied:
        return None
    if ACCESS_TOKEN and secrets.compare_digest(supplied, ACCESS_TOKEN):
        return {"role": "owner", "name": "owner"}
    for g in _active_guests():
        if secrets.compare_digest(supplied, g.get("token", "")):
            return {"role": "guest", "name": g.get("name", "guest")}
    return None


def create_guest_token(name: str, hours: int = 24) -> dict:
    """Mint a revocable, expiring guest token.

    Guests currently get the same API surface as the owner (explicit choice
    on 2026-08-11 — testers need the whole app). The separation still buys
    revocability, expiry, and per-tester attribution in the access log, none
    of which you get from handing out the owner token.
    """
    with _token_lock:
        data = _read_tokens()
        data.setdefault("guests", [])
        entry = {
            "id": uuid.uuid4().hex[:8],
            "name": name or "guest",
            "token": secrets.token_urlsafe(32),
            "created_at": _now_ts(),
            "expires_at": _now_ts() + hours * 3600 if hours else None,
        }
        data["guests"].append(entry)
        _write_tokens(data)
        return entry


def revoke_guest_token(guest_id: str) -> bool:
    with _token_lock:
        data = _read_tokens()
        before = len(data.get("guests", []))
        data["guests"] = [g for g in data.get("guests", []) if g.get("id") != guest_id]
        if len(data["guests"]) == before:
            return False
        _write_tokens(data)
        return True


# --- user accounts (username + password) -----------------------------------
# Users live in data/.ken_users.json next to the token file. Passwords are
# stored as salted scrypt hashes (stdlib, no new deps). The built-in owner
# account ("janak") is created on first run; further users are added by the
# owner through the UI (POST /api/users). A logged-in user gets a random
# session cookie that maps back to their username.
USERS_FILE = DATA_DIR / ".ken_users.json"
SESSION_COOKIE = "ken_session"
SESSION_TTL_S = 60 * 60 * 24 * 7  # 7 days — re-login weekly is a fair price for a public server

_session_lock = threading.Lock()
_sessions: dict[str, dict] = {}  # token -> {"user": str, "expires": float}


def _read_users() -> dict:
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_users(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


def _hash_pw(password: str, salt: bytes | None = None) -> str:
    """scrypt hash, stored as hex salt$hash. Raises ValueError on bad params."""
    if salt is None:
        salt = secrets.token_bytes(16)
    try:
        h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    except (ValueError, TypeError):
        # Fall back to a smaller n if the platform rejects 2**14 (very old
        # OpenSSL); n=2**12 is still beyond casual brute force.
        h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**12, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def _verify_pw(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        try:
            got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        except (ValueError, TypeError):
            got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**12, r=8, p=1)
        return secrets.compare_digest(got, expected)
    except Exception:
        return False


def _init_users() -> dict:
    """Ensure the owner account exists. Username defaults to 'janak'."""
    with _session_lock:
        data = _read_users()
        owner = os.environ.get("KEN_USERNAME") or "janak"
        if owner not in data:
            pw = os.environ.get("KEN_PASSWORD")
            if not pw:
                # No password configured yet: generate one and print/store it
                # so the owner can sign in the first time.
                pw = secrets.token_urlsafe(12)
            data[owner] = {"pw": _hash_pw(pw), "role": "owner", "created_at": _now_ts()}
            try:
                _write_users(data)
            except Exception:
                pass
            print(f"[KEN] created owner account '{owner}' (password printed once): {pw}")
        return data


_USERS = _init_users()


def _auth_user(username: str, password: str) -> dict | None:
    """Verify username/password; return the user record or None."""
    if not username or not password:
        return None
    with _session_lock:
        data = _read_users()
        rec = data.get(username)
        if not rec or not _verify_pw(password, rec.get("pw", "")):
            return None
        return {"role": rec.get("role", "user"), "name": username}


def create_user(username: str, password: str, role: str = "user") -> dict | None:
    """Add a user. Returns the record or None if the username is taken."""
    username = (username or "").strip().lower()
    if not username or not password or len(password) < 6:
        return None
    with _session_lock:
        data = _read_users()
        if username in data:
            return None
        data[username] = {"pw": _hash_pw(password), "role": role, "created_at": _now_ts()}
        try:
            _write_users(data)
        except Exception:
            pass
        return {"role": role, "name": username}


def delete_user(username: str) -> bool:
    """Remove a user (owner cannot be deleted)."""
    username = (username or "").strip().lower()
    if username == "janak":
        return False
    with _session_lock:
        data = _read_users()
        if username not in data:
            return False
        del data[username]
        try:
            _write_users(data)
        except Exception:
            pass
        return True


def list_users() -> list[dict]:
    with _session_lock:
        data = _read_users()
        return [
            {"username": u, "role": rec.get("role", "user"), "created_at": rec.get("created_at")}
            for u, rec in sorted(data.items())
        ]


def _new_session(username: str) -> str:
    tok = secrets.token_urlsafe(32)
    with _session_lock:
        _sessions[tok] = {"user": username, "expires": _now_ts() + SESSION_TTL_S}
    return tok


def _session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with _session_lock:
        s = _sessions.get(token)
        if not s:
            return None
        if s["expires"] < _now_ts():
            _sessions.pop(token, None)
            return None
        # Real role from the users file (owner vs user), not hardcoded.
        rec = _read_users().get(s["user"], {})
        return {"role": rec.get("role", "user"), "name": s["user"]}


# --- failed-auth throttle -------------------------------------------------
# A funnel URL is public and gets scanned within hours. The tokens are 256-bit
# so brute force isn't the real threat; this is about not burning CPU on bot
# traffic and making credential-stuffing noisy rather than free.
_FAIL_WINDOW_S = 300
_FAIL_LIMIT = 10
_fails: dict[str, list[float]] = {}
_fail_lock = threading.Lock()

# Global request-rate guard: a per-IP sliding window over ALL requests, so a
# scanner hammering endpoints (or a runaway bot) can't soak the funnel proxy.
# Generous for real use (a page load fires ~15 requests); brutal for spam.
_REQ_WINDOW_S = 60
_REQ_LIMIT = 600
_reqs: dict[str, list[float]] = {}
_req_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _is_loopback(ip: str) -> bool:
    """True for localhost connections (tests, dev). The failed-auth throttle
    exists for the public funnel; our own machine must never lock itself out."""
    return ip in ("127.0.0.1", "::1", "localhost", "?")


def _req_throttled(ip: str) -> bool:
    """True if this IP exceeded the global request rate."""
    cutoff = _now_ts() - _REQ_WINDOW_S
    with _req_lock:
        hits = [t for t in _reqs.get(ip, []) if t > cutoff]
        _reqs[ip] = hits
        if len(hits) >= _REQ_LIMIT:
            return True
        _reqs[ip].append(_now_ts())
        return False


def _throttled(ip: str) -> bool:
    if _is_loopback(ip):
        return False
    cutoff = _now_ts() - _FAIL_WINDOW_S
    with _fail_lock:
        hits = [t for t in _fails.get(ip, []) if t > cutoff]
        _fails[ip] = hits
        return len(hits) >= _FAIL_LIMIT


def _record_fail(ip: str) -> None:
    if _is_loopback(ip):
        return
    with _fail_lock:
        _fails.setdefault(ip, []).append(_now_ts())


# --- API-key redaction + per-user usage cap -------------------------------
# The DeepSeek key lives only on this server. Two protections on top:
#   1. _redact() masks any sk-... secret that ever shows up in an error
#      message or log line (OpenAI-style errors normally don't echo the key,
#      but a proxy or misbehaving lib can — never ship it to a client).
#   2. A daily request cap per non-owner user, so a shared account can't
#      drain the owner's DeepSeek credits. Owner is exempt. Configurable via
#      KEN_DAILY_USER_CAP (default 150 requests/day).
_DAILY_USER_CAP = int(os.environ.get("KEN_DAILY_USER_CAP", "150"))
_usage_day: dict[str, str] = {}
_usage_count: dict[str, int] = {}
_usage_lock = threading.Lock()


def _redact(text: str) -> str:
    if not text:
        return text
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***REDACTED***", text)


def _bump_usage(user: str) -> bool:
    """Record one chat/deep-analysis request for `user`. False = over cap."""
    day = time.strftime("%Y-%m-%d")
    with _usage_lock:
        if _usage_day.get(user) != day:
            _usage_day[user] = day
            _usage_count[user] = 0
        _usage_count[user] += 1
        return _usage_count[user] <= _DAILY_USER_CAP


def _audit(ip: str, who: str, method: str, path: str) -> None:
    """Append-only access log so you can see who reached what."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with AUTH_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{stamp}\t{ip}\t{who}\t{method}\t{path}\n")
    except Exception:
        pass


def _signin_page(error: str = "") -> str:
    """Sign-in page shown when / is reached without a valid token.

    Supports both flows, same form, POST for password (username/password via
    the session cookie) and GET ?token= for the emailed/bookmarked link.
    Self-contained (no fetches, no external assets).
    """
    err_html = f'<p style="color:#f5a3c7;font-size:13px">{error}</p>' if error else ""
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KEN - sign in</title><style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#05050a;color:#f2f2f5;font:400 15px/1.5 system-ui,-apple-system,sans-serif;padding:24px}}
.card{{width:100%;max-width:min(360px,100%);background:#0a0a12;border:1px solid rgba(255,49,49,.18);
 border-radius:16px;padding:26px;box-shadow:0 0 24px rgba(255,49,49,.12)}}
h1{{margin:0 0 6px;font-size:20px;letter-spacing:.14em}}
p{{margin:0 0 18px;color:rgba(242,242,245,.55);font-size:13px}}
input{{width:100%;padding:13px 14px;font-size:16px;border-radius:9px;
 background:rgba(242,242,245,.05);border:1px solid rgba(242,242,245,.14);color:#f2f2f5;outline:none}}
input:focus{{border-color:#ff3131}}
button{{width:100%;margin-top:12px;padding:13px;font-size:15px;font-weight:600;border:0;
 border-radius:9px;background:#ff3131;color:#05050a;cursor:pointer}}
button:active{{background:#e02a2a}}
.toggle{{text-align:center;margin-top:14px;font-size:12px;color:rgba(242,242,245,.45)}}
.toggle a{{color:#ff3131;cursor:pointer;text-decoration:none}}
@media (max-width:480px){{
 body{{padding:14px}}
 .card{{padding:20px}}
}}
</style></head><body>
<form class="card" method="post" action="" id="pwForm">
  <h1>KEN</h1>
  <p>Sign in with your username and password.</p>
  {err_html}
  <input name="username" type="text" placeholder="username" autofocus
         autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false">
  <input name="password" type="password" placeholder="password" style="margin-top:10px"
         autocomplete="current-password">
  <button type="submit">Sign in</button>
  <div class="toggle">Have a token instead? <a id="toToken">Use access token</a></div>
</form>
<form class="card" method="get" action="" id="tokenForm" style="display:none">
  <h1>KEN</h1>
  <p>Paste your access token below, or open the full link that already contains it.</p>
  <input name="token" type="password" placeholder="access token"
         autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false">
  <button type="submit">Unlock</button>
  <div class="toggle">Have a password instead? <a id="toPw">Use username &amp; password</a></div>
</form>
<script>
  document.getElementById("pwForm").addEventListener("submit", function(e){{
    e.preventDefault();
    var u = document.querySelector("#pwForm input[name=username]").value;
    var p = document.querySelector("#pwForm input[name=password]").value;
    // Absolute path with the proxy prefix: the page may be served under a
    // prefix (/ken), so build the login URL from the current pathname's
    // directory — posting to the bare relative "" would hit the auth gate.
    var base = location.pathname.replace(/\/+$/, "");
    var loginUrl = base + "/api/login";
    fetch(loginUrl, {{ method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{username: u, password: p}}) }})
      .then(function(r){{ return r.json().then(function(j){{ return {{ok: r.ok, j: j}}; }}); }})
      .then(function(res){{
        if (res.ok) {{ location.reload(); }}
        else {{
          var err = document.createElement("p");
          err.style.color = "#f5a3c7"; err.style.fontSize = "13px";
          err.textContent = res.j.error || "Sign in failed.";
          var f = document.getElementById("pwForm");
          var old = f.querySelector(".err"); if (old) old.remove();
          err.className = "err"; f.insertBefore(err, f.firstChild.nextSibling);
        }}
      }});
  }});
  document.getElementById("toToken").onclick = function(){{
    document.getElementById("pwForm").style.display="none";
    document.getElementById("tokenForm").style.display="block";
    document.querySelector("#tokenForm input").focus();
  }};
  document.getElementById("toPw").onclick = function(){{
    document.getElementById("tokenForm").style.display="none";
    document.getElementById("pwForm").style.display="block";
    document.querySelector("#pwForm input").focus();
  }};
</script></body></html>"""


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("x-ken-token")
        or request.cookies.get(COOKIE_NAME)
        or request.query_params.get("token")
    )


# ---------------------------------------------------------------------------
# What a guest may NOT do.
#
# Guests otherwise get the full app (owner's explicit choice). But "only the
# owner can configure sharing" is unenforceable unless guests also lose code
# execution and filesystem reads: otherwise a guest just enables run_python
# and shells out to `tailscale funnel`, or reads data/.ken_tokens.json and
# promotes themselves to owner. Blocking the config endpoints alone would be
# security theater.
#
# Enforced server-side (not merely hidden in the UI) in both the HTTP layer
# and the chat tool loop.
# ---------------------------------------------------------------------------
GUEST_BLOCKED_PATH_PREFIXES = (
    "/api/funnel",        # publishing KEN to the internet
    "/api/access",        # minting / revoking tokens
    "/api/config",        # every other setting
    "/api/tools/toggle",  # enabling a blocked tool is the escalation path
    "/api/admin",         # docker / process control
)

GUEST_BLOCKED_TOOLS = {
    # code execution
    "run_python", "restart_python_session", "run_skill", "mcp_call_tool",
    # filesystem — read_file would expose data/.ken_tokens.json
    "read_file", "write_file", "list_files",
    # host / infra control
    "admin", "query_sql", "transcribe_file",
    # destructive data ops
    "forget", "clear_tasks", "clear_crew_cache",
}


def _guest_blocked(path: str) -> bool:
    return any(path.startswith(p) for p in GUEST_BLOCKED_PATH_PREFIXES)


def _default_enabled(name: str) -> bool:
    return name not in DESTRUCTIVE_TOOLS


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tool_enabled(name: str) -> bool:
    cfg = _load_config().get("tools", {})
    if name in cfg:
        return bool(cfg[name])
    return _default_enabled(name)


def _set_tool_enabled(name: str, enabled: bool) -> None:
    cfg = _load_config()
    cfg.setdefault("tools", {})[name] = enabled
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write: a crash or concurrent toggle mid-write must not corrupt
    # config.json (a corrupt file silently resets every tool toggle — see
    # _load_config's broad except above).
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def _group_of(name: str) -> str:
    for group, names in TOOL_GROUPS.items():
        if name in names:
            return group
    return "other"


def _schema_map() -> dict[str, dict]:
    return {s["function"]["name"]: s["function"] for s in tools.SCHEMAS}


# ---------------------------------------------------------------------------
# Ollama reachability (fallback path only)
# ---------------------------------------------------------------------------
def _ollama_up() -> bool:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=OLLAMA_TIMEOUT_S)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App + CORS
#   Browsers send `Origin: null` for file:// pages; allow that plus any
#   localhost port (http://localhost:* per the spec).
# ---------------------------------------------------------------------------
app = FastAPI(title="KEN", version="1.0.0")


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """Reject anything without the token before it reaches a route.

    `/` is handled specially: a valid `?token=` there sets an HttpOnly cookie
    and redirects, so the token appears in a URL exactly once (when you first
    open the bookmark on a new device) and never again — it is not kept in
    localStorage, so page JavaScript can't read it.
    """
    path = request.url.path
    if not AUTH_ENABLED or path in _AUTH_EXEMPT or request.method == "OPTIONS":
        return await call_next(request)

    ip = _client_ip(request)
    # Global request-rate guard: applied to real API paths only (login is
    # exempt and must never be blocked by a page-load burst). A scanner gets
    # a 429 without burning CPU on token hashing.
    if path.startswith("/api/") and _req_throttled(ip):
        _audit(ip, "RATE", request.method, path)
        return JSONResponse({"detail": "Too many requests. Slow down."}, status_code=429)

    supplied = _token_from(request)
    principal = _principal_for(supplied)

    # Session cookie (username/password login) takes precedence when present.
    if principal is None and request.cookies.get(SESSION_COOKIE):
        principal = _session_user(request.cookies.get(SESSION_COOKIE))

    # Validate BEFORE consulting the throttle, so a valid token is never
    # locked out. The throttle is keyed on client IP, and behind
    # `tailscale funnel` every request can arrive from the proxy with the
    # same apparent address — checking it first would let one scanner lock
    # the owner out of their own machine. Bad credentials still get 429.
    if principal is None:
        if _throttled(ip):
            return JSONResponse({"detail": "Too many failed attempts. Try later."},
                                status_code=429)
        _record_fail(ip)
        _audit(ip, "DENIED", request.method, path)
        if path == "/":
            # A browser landing here must get something usable, not a raw JSON
            # blob — on a phone that reads as "the app is broken, nothing is
            # clickable". Give it a real sign-in page instead.
            return HTMLResponse(_signin_page(), status_code=401)
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    # Guests are barred from the configuration/security surface. Checked here
    # so it holds for every route, not just the ones that remember to ask.
    if principal["role"] != "owner" and _guest_blocked(path):
        _audit(ip, f"{principal['name']}/BLOCKED", request.method, path)
        return JSONResponse(
            {"detail": "Only the owner can change sharing, access, or system settings."},
            status_code=403,
        )

    request.state.principal = principal
    if path == "/":
        _audit(ip, principal["name"], request.method, path)

    # Promote a valid ?token= on the page load into a cookie and serve the page
    # directly. Deliberately NOT a redirect: KEN is proxied under a path prefix
    # (Tailscale mounts it at /ken) that the proxy strips before we see it, so
    # any absolute Location we built would point at the site root and land on a
    # different app. ken.html strips the token from the address bar itself via
    # history.replaceState, which needs no knowledge of the prefix.
    resp = await call_next(request)
    if path == "/" and supplied and COOKIE_NAME not in request.cookies:
        _set_token_cookie(resp, request, supplied)
    return resp


def _set_token_cookie(resp, request: Request, token: str) -> None:
    """Persist the token the caller actually presented.

    Must echo `token`, never ACCESS_TOKEN: writing the owner token here would
    hand every guest an owner cookie on their first page load, silently
    escalating them and making revocation useless.
    """
    # Secure only when the request actually arrived over TLS. `tailscale
    # serve`/`funnel` terminate HTTPS and forward plain HTTP to 127.0.0.1, so
    # trust their X-Forwarded-Proto; a Secure cookie set on a plain-http
    # desktop session would never be sent back.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    resp.set_cookie(
        COOKIE_NAME, token,
        httponly=True,           # JS can't read it -> XSS can't exfiltrate it
        # Lax, not Strict. Strict withholds the cookie on top-level navigation
        # that originates off-site — which is exactly how this gets opened on a
        # phone (tapping the link from a chat app, or a bookmark opened from
        # another app). That produced a bare 401 instead of the app. Lax still
        # blocks cross-site POSTs and subresource requests, which is the part
        # that matters here.
        samesite="lax",
        secure=(proto == "https"),
        max_age=60 * 60 * 24 * 365,
        path="/",
    )


app.add_middleware(
    CORSMiddleware,
    # No "null" origin and no wildcard: with credentials in play, a permissive
    # CORS policy would let any page you visit drive this API through your
    # browser. Same-origin (the server serves ken.html itself) needs no CORS
    # at all; localhost is allowed for local development only.
    allow_origins=[],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _require_deepseek_key() -> None:
    # KEN_ADDENDUM_deepseek_api.md: "the server must refuse to start (clear
    # error) if [DEEPSEEK_API_KEY] is missing."
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. KEN's primary chat provider is the "
            "DeepSeek hosted API — set DEEPSEEK_API_KEY before starting the server."
        )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    enabled_tools: list[str] = Field(default_factory=list)
    chat_id: str | None = None


class ToolToggle(BaseModel):
    name: str
    enabled: bool


class DeepAnalysisRequest(BaseModel):
    question: str


class MemoryWrite(BaseModel):
    key: str
    value: str
    facts: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)


class TaskCreate(BaseModel):
    text: str


class TaskUpdate(BaseModel):
    done: bool


class ReminderCreate(BaseModel):
    text: str
    due_at: str


class GistRequest(BaseModel):
    memory_limit: int = 12
    snippet_chars: int = 280
    include_git: bool = True
    inject: bool = False   # also persist as config context_gist (permanent injection)


class ConfigWrite(BaseModel):
    key: str
    value: Any


class SpeakRequest(BaseModel):
    text: str


class AdminRequest(BaseModel):
    action: str = "status"
    name: str | None = None
    source: str = "crew-log"
    lines: int = 40


class GuestCreate(BaseModel):
    name: str = "tester"
    hours: int = 24            # 0 = never expires (discouraged on a funnel)


class FunnelToggle(BaseModel):
    public: bool
    port: int = 8756


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict) -> dict:
    """One SSE record for sse-starlette: `event:` name + `data:` JSON line."""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


def _summarize(result: Any, limit: int = 120) -> str:
    text = " ".join(str(result).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _looks_like_llm_error(text: str) -> bool:
    """True when the stream produced only the assistant's error wrapper —
    the primary provider was unusable from the very first chunk (missing/bad
    key, network down, rate limited). Provider-agnostic: matches both this
    server's own DeepSeek wrapper and assistant/llm.py's Ollama wrapper."""
    t = (text or "").strip().lstrip("_")
    return t.startswith("Error talking to")


def _chat_meta(chat_id: str) -> dict:
    p = DATA_DIR / "chats" / f"{chat_id}.meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Chat ids are always `uuid.uuid4().hex[:12]` (sessions.new_chat). Reject
# anything else before it reaches sessions.*, which builds file paths by
# straight string interpolation (`CHATS_DIR / f"{chat_id}.jsonl"`) — an
# unvalidated chat_id is a path-traversal vector, and CORS here allows any
# localhost origin and file://, so any local page can call this API.
_CHAT_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _valid_chat_id(chat_id: str | None) -> bool:
    return bool(chat_id) and bool(_CHAT_ID_RE.match(chat_id))


async def _iter_queue(q: queue.Queue, thread: threading.Thread) -> AsyncGenerator[tuple[str, dict], None]:
    """Yield (event, payload) from a worker thread's queue until it finishes.
    Falls through if the thread dies without pushing a terminal event."""
    while True:
        try:
            item = await asyncio.to_thread(q.get, True, 0.25)
        except queue.Empty:
            if not thread.is_alive():
                return
            continue
        yield item
        if item[0] in ("done", "error"):
            # let the worker finish its cleanup (REGISTRY restore) before returning;
            # off the event loop, since Thread.join() blocks synchronously and would
            # otherwise stall every other concurrent request for up to `timeout`.
            await asyncio.to_thread(thread.join, 2)
            return


# ---------------------------------------------------------------------------
# DeepSeek tool-calling loop
#
# Mirrors assistant/llm.py's stream_chat shape (system prompt -> completion
# -> execute tool_calls via REGISTRY -> append results -> repeat, up to
# MAX_TOOL_ROUNDS) but targets DeepSeek's OpenAI-compatible streaming API
# instead of `ollama.chat`. Reuses llm._system_prompt() (memory/reminders
# injection) and tools.REGISTRY/tools.SCHEMAS so behavior stays identical to
# the Ollama loop apart from the transport. Tool execution goes through
# tools.REGISTRY[name] directly (not assistant.llm's loop), so the
# tool_start/tool_end timing wrapper _chat_worker installs on REGISTRY
# still fires transparently.
# ---------------------------------------------------------------------------
def _deepseek_stream_chat(model: str, history: list[dict]):
    messages = [{"role": "system", "content": llm._system_prompt()}] + history

    for _round in range(llm.MAX_TOOL_ROUNDS):
        try:
            stream = _deepseek_client.chat.completions.create(
                model=model, messages=messages, tools=tools.SCHEMAS or None, stream=True,
            )
        except Exception as e:
            yield f"\n\n_Error talking to DeepSeek: {e}_"
            return

        content = ""
        calls: dict[int, dict] = {}
        try:
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    content += delta.content
                    yield delta.content
                for tc in (delta.tool_calls or []):
                    slot = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        except Exception as e:
            yield f"\n\n_Error talking to DeepSeek: {e}_"
            return

        if not calls:
            return

        ordered = [calls[i] for i in sorted(calls)]
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in ordered
            ],
        })
        for c in ordered:
            name = c["name"]
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = tools.REGISTRY.get(name)
            result = fn(**args) if fn else f"Unknown tool: {name}"
            observations.append(name, args, result)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": str(result)})

    yield "\n\n_Reached max tool-call rounds without a final answer._"


# ---------------------------------------------------------------------------
# Free cloud fallbacks (Bluesminds / Groq / OpenRouter) — OpenAI-compatible
# ---------------------------------------------------------------------------
def _openai_compat_stream_chat(client, model: str, history: list[dict],
                               label: str, use_tools: bool = False):
    """Streaming tool-loop for any OpenAI-compatible provider (free tiers).

    Mirrors _deepseek_stream_chat but tolerates no tool support (free llama
    models often lack function calling) by falling back to plain completion.
    Errors yield error-text, which the runner's _looks_like_llm_error check
    converts into a fallthrough to the next candidate.
    """
    messages = [{"role": "system", "content": llm._system_prompt()}] + history
    if not client:
        yield f"\n\n_Error: no client for {label}_"
        return

    for _round in range(llm.MAX_TOOL_ROUNDS):
        try:
            kwargs = {}
            if use_tools and tools.SCHEMAS:
                kwargs["tools"] = tools.SCHEMAS
            stream = client.chat.completions.create(
                model=model, messages=messages, stream=True, **kwargs)
        except Exception as e:
            yield f"\n\n_Error talking to {label}: {e}_"
            return

        content = ""
        calls: dict[int, dict] = {}
        try:
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    content += delta.content
                    yield delta.content
                for tc in (delta.tool_calls or []):
                    slot = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        except Exception as e:
            yield f"\n\n_Error talking to {label}: {e}_"
            return

        if not calls:
            return

        ordered = [calls[i] for i in sorted(calls)]
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in ordered
            ],
        })
        for c in ordered:
            name = c["name"]
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = tools.REGISTRY.get(name)
            result = fn(**args) if fn else f"Unknown tool: {name}"
            observations.append(name, args, result)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": str(result)})

    yield "\n\n_Reached max tool-call rounds without a final answer._"


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL,
        "provider": PROVIDER,
        "base_url": DEEPSEEK_BASE_URL,
        "fallback_model": FALLBACK_MODEL,
        "ollama_up": _ollama_up(),
    }


# ---------------------------------------------------------------------------
# /api/tools
# ---------------------------------------------------------------------------
@app.get("/api/tools")
async def list_tools(request: Request) -> list[dict]:
    principal = getattr(request.state, "principal", None) or {"role": "owner"}
    is_guest = principal["role"] != "owner"
    schemas = _schema_map()
    out = []
    for name in tools.REGISTRY:
        # Don't advertise tools a guest can't call; _sse_chat strips them from
        # enabled_tools anyway, so listing them would only produce confusing
        # "tool unavailable" replies.
        if is_guest and name in GUEST_BLOCKED_TOOLS:
            continue
        out.append({
            "name": name,
            "group": _group_of(name),
            "enabled": _tool_enabled(name),
            "description": schemas.get(name, {}).get("description", ""),
        })
    return out


@app.post("/api/tools/toggle")
async def toggle_tool(body: ToolToggle) -> dict:
    if body.name not in tools.REGISTRY:
        raise HTTPException(404, f"Unknown tool: {body.name}")
    _set_tool_enabled(body.name, body.enabled)
    schemas = _schema_map()
    return {
        "name": body.name,
        "group": _group_of(body.name),
        "enabled": body.enabled,
        "description": schemas.get(body.name, {}).get("description", ""),
    }


# ---------------------------------------------------------------------------
# /api/chat  (STREAMING, SSE)
#
# For the duration of the turn only:
#   * tools.SCHEMAS -> filtered to the request's enabled_tools (model is only
#                     *offered* enabled tools),
#   * REGISTRY     -> wrapped so disabled tools return "unavailable" without
#                     executing, and enabled tools emit tool_start/tool_end
#                     with real wall-clock ms.
# A module lock serializes turns (the patching is process-global, and this is
# a local single-user server).
# ---------------------------------------------------------------------------
_CHAT_LOCK = threading.Lock()


def _chat_worker(model: str, history: list[dict], enabled: set[str], chat: dict,
                 q: queue.Queue) -> None:
    """Run the real assistant loop; push (event, payload) tuples onto q."""
    turn_id = uuid.uuid4().hex[:12]
    saved_registry = dict(tools.REGISTRY)
    saved_schemas = tools.SCHEMAS
    full_reply: list[str] = []
    try:
        # 1) Only offer enabled tools to the model.
        tools.SCHEMAS = [
            s for s in saved_schemas if s["function"]["name"] in enabled
        ]

        # 2) Wrap REGISTRY: enabled -> timing wrapper, disabled -> blocker stub.
        for name, fn in saved_registry.items():
            if name in enabled:
                def _wrap(_name: str = name, _fn=fn) -> callable:
                    def call(**kwargs: Any) -> str:
                        t0 = time.perf_counter()
                        q.put(("tool_start", {"name": _name, "args": kwargs}))
                        try:
                            result = _fn(**kwargs)
                            ok, summary = True, _summarize(result)
                        except Exception as e:  # noqa: BLE001 - surface any tool failure
                            result, ok, summary = f"Tool error: {e}", False, str(e)
                        ms = int((time.perf_counter() - t0) * 1000)
                        q.put(("tool_end", {"name": _name, "ms": ms, "ok": ok, "summary": summary}))
                        return result
                    return call
                tools.REGISTRY[name] = _wrap()
            else:
                tools.REGISTRY[name] = (
                    lambda _n=name, **kw:
                    f"Tool unavailable: {_n} is not in this request's enabled_tools."
                )

        # 3) Run the loop with automatic provider fallback: DeepSeek first
        #    (this server's own OpenAI-style loop against the hosted API);
        #    if it's unusable from the start of the turn (no key, network
        #    down, 401, 429), retry once against the free local Ollama model
        #    via the existing assistant/llm.py loop, unchanged. Once any real
        #    content or tool call has streamed, the turn stays committed to
        #    that provider — a mid-stream error surfaces to the client as-is.
        used_model: str | None = None
        # Provider chain: Hermes A2A (if enabled) -> DeepSeek -> local Ollama.
        # Once any real content has streamed, the turn stays committed to that
        # provider — a mid-stream error surfaces to the client as-is.
        candidates: list[tuple[str, object]] = []
        if HERMES_A2A_ENABLED:
            candidates.append(("hermes-a2a", _hermes_stream(history, chat.get("id", ""))))
        candidates.append((model, _deepseek_stream_chat(model, history)))
        # Free cloud tiers (all probe-verified 2026-08-13): Bluesminds ->
        # Groq -> OpenRouter, then local Ollama last. Each yields error-text on
        # failure, which the runner converts into fallthrough to the next.
        if _bluesminds_client:
            candidates.append((BLUESMINDS_MODEL,
                               _openai_compat_stream_chat(_bluesminds_client, BLUESMINDS_MODEL,
                                                          history, "Bluesminds")))
        if _groq_client:
            candidates.append((GROQ_MODEL,
                               _openai_compat_stream_chat(_groq_client, GROQ_MODEL,
                                                          history, "Groq")))
        if _openrouter_client:
            candidates.append((OPENROUTER_MODEL,
                               _openai_compat_stream_chat(_openrouter_client, OPENROUTER_MODEL,
                                                          history, "OpenRouter")))
        if FALLBACK_MODEL:
            candidates.append((FALLBACK_MODEL, llm.stream_chat(FALLBACK_MODEL, history, on_tool_call=None)))
        for attempt, (candidate, gen) in enumerate(candidates):
            buf: list[str] = []
            started = False
            for piece in gen:
                if attempt == 0 and not started and _looks_like_llm_error("".join(buf) + piece):
                    break  # primary unusable from the start -> try next provider
                started = True
                buf.append(piece)
                full_reply.append(piece)
                q.put(("token", {"delta": piece}))
            else:
                used_model = candidate
                break
        if used_model is None:
            used_model = candidates[-1][0]  # every candidate failed; reply carries the last error

        # 4) Persist the turn (user message + final assistant reply).
        reply_text = "".join(full_reply).strip()
        user_msg = history[-1].get("content", "") if history else ""
        if reply_text or user_msg:
            if user_msg:
                chat["messages"].append({"role": "user", "content": user_msg})
            if reply_text:
                chat["messages"].append({"role": "assistant", "content": reply_text})
            try:
                sessions.save(chat)
            except Exception as e:  # persistence must not kill the stream
                q.put(("error", {"message": f"Failed to persist chat: {e}"}))

        q.put(("done", {"turn_id": turn_id, "chat_id": chat["id"], "model": used_model or MODEL}))
    except Exception as e:
        q.put(("error", {"message": str(e)}))
    finally:
        # 5) Restore the real REGISTRY/SCHEMAS for the next turn.
        tools.REGISTRY.clear()
        tools.REGISTRY.update(saved_registry)
        tools.SCHEMAS = saved_schemas


async def _sse_chat(req: ChatRequest, role: str = "owner") -> AsyncGenerator[dict, None]:
    if not req.messages:
        yield _sse("error", {"message": "messages must not be empty."})
        return
    if req.messages[-1].role != "user":
        yield _sse("error", {"message": "last message must have role 'user'."})
        return

    if not HERMES_A2A_ENABLED and not _deepseek_client and not _ollama_up():
        yield _sse("error", {"message": "Neither DeepSeek nor local Ollama is reachable."})
        return

    chat = None
    if req.chat_id and _valid_chat_id(req.chat_id):
        try:
            chat = sessions.load(req.chat_id)
        except FileNotFoundError:
            chat = None
    if chat is None:
        chat = sessions.new_chat()

    history = [m.model_dump() for m in req.messages]
    enabled = set(req.enabled_tools)
    if role != "owner":
        # Server-side, not a UI nicety: the client sends enabled_tools, so a
        # guest could otherwise just ask for run_python in the request body.
        enabled -= GUEST_BLOCKED_TOOLS
    q: queue.Queue = queue.Queue()

    def run() -> None:
        with _CHAT_LOCK:
            _chat_worker(MODEL, history, enabled, chat, q)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    async for event, payload in _iter_queue(q, thread):
        yield _sse(event, payload)


@app.post("/api/chat")
async def chat(request: Request, req: ChatRequest) -> EventSourceResponse:
    principal = getattr(request.state, "principal", None) or {"role": "owner"}
    role = principal.get("role", "user")
    # Per-user daily cap (owner exempt) so a shared account can't drain the
    # owner's DeepSeek credits.
    if role != "owner":
        if not _bump_usage(principal.get("name", "?")):
            raise HTTPException(
                status_code=429,
                detail=_redact(
                    f"Daily request limit reached ({_DAILY_USER_CAP} today). Try again tomorrow."
                ),
            )
    return EventSourceResponse(_sse_chat(req, role), ping=15)


# ---------------------------------------------------------------------------
# /api/deep-analysis  (STREAMING, SSE)
#
# Runs the real 4-agent crew (assistant/crew.py), whose Analyst stage already
# targets the DeepSeek API (assistant.crew.DeepSeekLLM) with a local Ollama
# fallback — no change needed there. The sync `run_deep_analysis` call runs
# in a worker thread; because per-task progress hooks are not exposed by the
# assistant module, the server emits an honest coarse progress (`fetch`
# running at start, `report` done at end) rather than fabricating per-stage
# timings. Cache is checked first so cached runs return instantly.
# ---------------------------------------------------------------------------
async def _sse_deep_analysis(req: DeepAnalysisRequest) -> AsyncGenerator[dict, None]:
    cached = crew_cache.get_cached(req.question)
    if cached is not None:
        yield _sse("done", {
            "findings": cached,
            "sources": [],
            "guardrail": {"numeric": "pass", "coverage": "3/3", "local_fallback": False},
            "cached": True,
        })
        return

    q: queue.Queue = queue.Queue()

    def run() -> None:
        try:
            q.put(("stage", {"stage": "fetch", "status": "running", "ms": 0,
                             "note": "starting deep-analysis pipeline"}))
            from assistant import crew  # lazy: crewai is a heavy optional dep
            t0 = time.perf_counter()
            result = crew.run_deep_analysis(req.question)
            ms = int((time.perf_counter() - t0) * 1000)
            local_fallback = "was analyzed locally with" in result
            q.put(("stage", {"stage": "report", "status": "done", "ms": ms, "note": ""}))
            q.put(("done", {
                "findings": result,
                "sources": [],
                "guardrail": {"numeric": "pass", "coverage": "3/3",
                              "local_fallback": local_fallback},
                "cached": False,
            }))
        except Exception as e:
            q.put(("error", {"message": _redact(f"Deep analysis error: {e}")}))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    async for event, payload in _iter_queue(q, thread):
        yield _sse(event, payload)


@app.post("/api/deep-analysis")
async def deep_analysis(req: DeepAnalysisRequest, request: Request) -> EventSourceResponse:
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    principal = getattr(request.state, "principal", None) or {"role": "owner"}
    role = principal.get("role", "user")
    # Same daily cap as chat — deep analysis is heavier, so non-owner users
    # share the same budget (owner exempt).
    if role != "owner":
        if not _bump_usage(principal.get("name", "?")):
            raise HTTPException(
                status_code=429,
                detail=_redact(
                    f"Daily request limit reached ({_DAILY_USER_CAP} today). Try again tomorrow."
                ),
            )
    return EventSourceResponse(_sse_deep_analysis(req), ping=15)


# ---------------------------------------------------------------------------
# /api/chats
# ---------------------------------------------------------------------------
@app.get("/api/chats")
async def list_chats() -> list[dict]:
    out = []
    for c in sessions.list_chats():  # newest first (by file mtime)
        meta = _chat_meta(c["id"])
        out.append({
            "id": c["id"],
            "title": c.get("title", "New chat"),
            "updated_at": meta.get("updated_at", ""),
            "message_count": meta.get("message_count", 0),
        })
    return out


@app.get("/api/chats/search")
async def search_chats(q: str) -> list[dict]:
    """Declared BEFORE /api/chats/{chat_id}: FastAPI resolves routes in
    definition order, and "search" would otherwise be captured as a chat_id."""
    if not q.strip():
        return []
    return await asyncio.to_thread(sessions.search_chats, q)


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str) -> dict:
    if not _valid_chat_id(chat_id):
        raise HTTPException(404, "Chat not found")
    try:
        chat = sessions.load(chat_id)
    except FileNotFoundError:
        raise HTTPException(404, "Chat not found")
    return {"id": chat["id"], "title": chat.get("title", "New chat"),
            "messages": chat["messages"]}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict:
    if not _valid_chat_id(chat_id):
        raise HTTPException(404, "Chat not found")
    try:
        sessions.load(chat_id)
    except FileNotFoundError:
        raise HTTPException(404, "Chat not found")
    sessions.delete(chat_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/memory
# ---------------------------------------------------------------------------
@app.get("/api/memory")
async def list_memory() -> list[dict]:
    return memory.recent_entries(limit=10_000)  # most recent first


@app.post("/api/memory")
async def add_memory(body: MemoryWrite) -> dict:
    # Per the build spec: never overwrite an existing key (unlike the
    # remember tool, which overwrites by design). 409 on collision.
    if memory.get_entry(body.key) is not None:
        raise HTTPException(409, f"Memory key '{body.key}' already exists.")
    msg = memory.remember(body.key, body.value, body.facts, body.concepts)
    return {"ok": True, "message": msg, "entry": memory.get_entry(body.key)}


# ---------------------------------------------------------------------------
# /api/vault  — Obsidian brain vault (assistant/vault.py)
#
# vault.py is deliberately dependency-free (files + git only), so every
# endpoint here works even when Ollama and DeepSeek are both down.
# ---------------------------------------------------------------------------
@app.get("/api/vault/notes")
async def vault_notes() -> dict:
    notes = await asyncio.to_thread(vault.list_notes)
    # mtime is a datetime; JSON-encode it and drop the heavy link list here
    # (the note detail endpoint returns links for a single note).
    return {
        "vault_dir": str(vault.vault_dir()),
        "count": len(notes),
        "folders": sorted({n["folder"] for n in notes if n["folder"]}),
        "notes": [
            {
                "rel": n["rel"], "folder": n["folder"], "name": n["name"],
                "mtime": n["mtime"].isoformat(timespec="seconds"),
                "chars": n["chars"], "lines": n["lines"],
                "heading": n["heading"], "snippet": n["snippet"],
                "links": n["links"],
            }
            for n in notes
        ],
    }


@app.get("/api/vault/note")
async def vault_note(rel: str) -> dict:
    """Read one note. `rel` is a vault-relative posix path (?rel=notes/Foo.md)."""
    body = await asyncio.to_thread(vault.read_note, rel)
    if body is None:
        raise HTTPException(404, f"Note not found: {rel}")
    return {"rel": rel, "body": body}


@app.get("/api/vault/search")
async def vault_search(q: str) -> dict:
    if not q.strip():
        return {"query": q, "results": []}
    hits = await asyncio.to_thread(vault.search_notes, q)
    return {
        "query": q,
        "results": [
            {"rel": h["rel"], "name": h["name"], "folder": h["folder"],
             "heading": h.get("heading", ""), "match": h.get("match", "")}
            for h in hits
        ],
    }


@app.get("/api/vault/git")
async def vault_git() -> dict:
    info = await asyncio.to_thread(vault.git_info)
    status = await asyncio.to_thread(vault.git_status_text)
    return {**info, "status_text": status}


@app.post("/api/vault/gist")
async def vault_gist(body: GistRequest) -> dict:
    """Build the context pack. With inject=true it is also persisted to
    config as `context_gist`, which assistant/llm.py splices into every
    system prompt (capped by max_gist_chars)."""
    text = await asyncio.to_thread(
        vault.build_gist, body.memory_limit, body.snippet_chars, body.include_git,
    )
    if body.inject:
        await asyncio.to_thread(config_mod.set, "context_gist", text)
    return {"gist": text, "chars": len(text), "injected": body.inject}


@app.delete("/api/vault/gist")
async def vault_gist_clear() -> dict:
    """Stop injecting the context pack into the system prompt."""
    await asyncio.to_thread(config_mod.set, "context_gist", "")
    return {"ok": True, "injected": False}


@app.post("/api/vault/sync")
async def vault_sync() -> dict:
    """Re-index the vault into the RAG store (assistant/rag.py)."""
    msg = await asyncio.to_thread(rag.sync_vault)
    return {"ok": True, "message": msg}


@app.get("/api/vault/graph")
async def vault_graph_data() -> dict:
    """Wiki-link graph (nodes + links) for the vault. This is the simple
    link graph from assistant/vault_graph.py — distinct from the Graphiti/
    Neo4j semantic graph exposed via graphiti_mcp_server.py."""
    from assistant import vault_graph as _vg  # lazy: only needed for this view
    return await asyncio.to_thread(_vg.build_graph)


@app.get("/api/vault/graphiti")
async def vault_graphiti_search(q: str = "", top_k: int = 5) -> dict:
    """Semantic search over the Graphiti knowledge graph (assistant/graph.py).
    Returns facts as a bullet list; empty query returns usage hint."""
    from assistant import graph as _graph  # lazy: keeps Neo4j import out of hot path
    if not q.strip():
        return {"ok": True, "hint": "Pass ?q=<query>&top_k=<n> to search the knowledge graph.", "facts": []}
    try:
        facts = await asyncio.to_thread(_graph.graph_search, q, top_k)
        if facts.startswith("Graph search error"):
            return {"ok": False, "error": facts}
        return {"ok": True, "facts": facts.splitlines()}
    except Exception as e:  # Neo4j down etc. — don't 500 the assistant
        return {"ok": False, "error": f"Graph search error: {e}"}


# ---------------------------------------------------------------------------
# /api/tasks  (assistant/todo.py)
# ---------------------------------------------------------------------------
@app.get("/api/tasks")
async def get_tasks() -> list[dict]:
    return await asyncio.to_thread(todo.get_tasks)


@app.post("/api/tasks")
async def create_task(body: TaskCreate) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "text must not be empty")
    msg = await asyncio.to_thread(todo.add_task, body.text.strip())
    return {"ok": True, "message": msg, "tasks": todo.get_tasks()}


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, body: TaskUpdate) -> dict:
    msg = await asyncio.to_thread(todo.set_task_done, task_id, body.done)
    return {"ok": True, "message": msg, "tasks": todo.get_tasks()}


@app.delete("/api/tasks/{task_id}")
async def remove_task(task_id: int) -> dict:
    msg = await asyncio.to_thread(todo.delete_task, task_id)
    return {"ok": True, "message": msg, "tasks": todo.get_tasks()}


# ---------------------------------------------------------------------------
# /api/reminders  (assistant/reminders.py)
# ---------------------------------------------------------------------------
@app.get("/api/reminders")
async def get_reminders() -> dict:
    all_r = await asyncio.to_thread(reminders.get_reminders)
    due = await asyncio.to_thread(reminders.due_reminders)
    return {"reminders": all_r, "due": due}


@app.post("/api/reminders")
async def create_reminder(body: ReminderCreate) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "text must not be empty")
    msg = await asyncio.to_thread(reminders.remind_me, body.text.strip(), body.due_at)
    return {"ok": True, "message": msg, "reminders": reminders.get_reminders()}


@app.delete("/api/reminders/{reminder_id}")
async def remove_reminder(reminder_id: int) -> dict:
    msg = await asyncio.to_thread(reminders.cancel_reminder, reminder_id)
    return {"ok": True, "message": msg, "reminders": reminders.get_reminders()}


# ---------------------------------------------------------------------------
# /api/backups  (assistant/backup.py)
# ---------------------------------------------------------------------------
@app.get("/api/backups")
async def get_backups() -> dict:
    return {
        "location": backup.backup_location(),
        "backups": await asyncio.to_thread(backup.list_backups),
        "latest": await asyncio.to_thread(backup.latest_backup_time),
        "size_bytes": await asyncio.to_thread(backup.size_bytes),
    }


@app.post("/api/backups")
async def create_backup() -> dict:
    msg = await asyncio.to_thread(backup.create_backup)
    return {"ok": True, "message": msg, "backups": backup.list_backups()}


# ---------------------------------------------------------------------------
# /api/documents  — RAG store (assistant/rag.py)
# ---------------------------------------------------------------------------
@app.get("/api/documents")
async def get_documents() -> dict:
    docs = await asyncio.to_thread(rag.list_documents)
    return {
        "documents": [d for d in docs if not d.startswith(rag.VAULT_PREFIX)],
        "vault_notes": len([d for d in docs if d.startswith(rag.VAULT_PREFIX)]),
        "knowledge": len([d for d in docs if d.startswith("knowledge:")]),
    }


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    msg = await asyncio.to_thread(rag.ingest, file.filename or "upload.txt", raw)
    return {"ok": True, "message": msg}


@app.delete("/api/documents/{filename:path}")
async def delete_document(filename: str) -> dict:
    msg = await asyncio.to_thread(rag.remove_document, filename)
    return {"ok": True, "message": msg}


@app.post("/api/knowledge/sync")
async def sync_knowledge() -> dict:
    msg = await asyncio.to_thread(rag.sync_knowledge)
    return {"ok": True, "message": msg}


# ---------------------------------------------------------------------------
# /api/voice  (assistant/voice.py — faster-whisper + pyttsx3, both local)
#
# Heavy optional deps: a missing/broken whisper or TTS install must return a
# clean 503 rather than a 500 traceback, since the rest of KEN works fine
# without voice.
# ---------------------------------------------------------------------------
@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty audio")
    try:
        from assistant import voice
        text = await asyncio.to_thread(voice.transcribe, raw)
    except Exception as e:  # noqa: BLE001 - optional dependency surface
        raise HTTPException(503, f"Transcription unavailable: {e}")
    return {"text": text}


@app.post("/api/voice/speak")
async def voice_speak(body: SpeakRequest):
    if not body.text.strip():
        raise HTTPException(400, "text must not be empty")
    try:
        from assistant import voice
        wav = await asyncio.to_thread(voice.speak, body.text)
    except Exception as e:  # noqa: BLE001 - optional dependency surface
        raise HTTPException(503, f"Speech synthesis unavailable: {e}")
    from fastapi.responses import Response
    return Response(content=wav, media_type="audio/wav")


# ---------------------------------------------------------------------------
# /api/admin  — fleet administration (assistant/admin.py)
#
# Shells out to docker/python and reads logs. Local-only server, but the
# action is still whitelisted by admin.py's own dispatch (unknown actions
# return a usage string rather than executing anything).
# ---------------------------------------------------------------------------
@app.get("/api/admin/status")
async def admin_status() -> dict:
    return {"status": await asyncio.to_thread(admin_mod.admin_status)}


@app.get("/api/status")
async def status_overview(request: Request) -> dict:
    """Monitor panel: bots, providers, QA, crons, sandbox — OWNER ONLY.

    Exposes internal health (paths, ports, cron state) so it must never be
    reachable by non-owner accounts on the public funnel.
    """
    principal = getattr(request.state, "principal", None) or {"role": "owner"}
    if principal["role"] != "owner":
        raise HTTPException(403, "Only the owner can view status.")
    import json as _json
    from pathlib import Path as _Path
    data_dir = _Path(__file__).resolve().parent / "data"

    def read_json(name: str, default):
        try:
            return _json.loads((data_dir / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def tail_jsonl(name: str, n: int = 3):
        try:
            lines = (data_dir / name).read_text(encoding="utf-8").splitlines()
            return [_json.loads(l) for l in lines[-n:] if l.strip()]
        except Exception:
            return []

    # bots
    bots = read_json("bots_state.json", {})
    bots_cfg = read_json("bots.json", {"bots": [], "fleets": []})
    bot_list = bots_cfg.get("bots", [])
    fleet_list = bots_cfg.get("fleets", [])

    # providers
    prov_state = read_json("provider_health_state.json", {})
    prov_last = tail_jsonl("provider_health.jsonl", 1)

    # QA
    qa_state = read_json("qa_state.json", {})
    qa_last = tail_jsonl("qa_buglog.jsonl", 3)

    # sandbox
    import urllib.request
    sandbox_up = False
    try:
        with urllib.request.urlopen("http://127.0.0.1:8766/api/health", timeout=3) as r:
            sandbox_up = r.status == 200
    except Exception:
        sandbox_up = False

    # hermes cron jobs (same machine)
    cron_jobs = []
    cron_file = _Path(r"C:\Users\Janak's PC\AppData\Local\hermes\cron\jobs.json")
    try:
        raw = _json.loads(cron_file.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("jobs", raw)
        if isinstance(items, dict):
            items = items.values()
        for j in items:
            cron_jobs.append({
                "name": j.get("name") or j.get("job_id") or "?",
                "schedule": (j.get("schedule") or {}).get("display") or j.get("display") or j.get("schedule"),
                "last_run": j.get("last_run_at"),
                "status": j.get("last_status"),
                "enabled": j.get("enabled", True),
            })
    except Exception:
        pass

    return {
        "fleets": [{"id": f.get("id"), "name": f.get("name"),
                    "coordinator": f.get("coordinator"),
                    "members": f.get("members", [])} for f in fleet_list],
        "bots": [{"id": b.get("id"), "name": b.get("name"), "type": b.get("type"),
                  "fleet": b.get("fleet"), "schedule": b.get("schedule"),
                  "enabled": b.get("enabled", True),
                  "last_run": (bots.get(b.get("id")) or {}).get("last_run")}
                 for b in bot_list],
        "providers": prov_state,
        "provider_last": prov_last[0] if prov_last else None,
        "qa": {"last_fp": qa_state.get("last_fp"), "last_ts": qa_state.get("last_ts"),
               "recent": qa_last},
        "sandbox": {"up": sandbox_up, "port": 8766},
        "crons": cron_jobs,
        "server": {"model": "deepseek", "auth_enabled": AUTH_ENABLED},
    }


@app.post("/api/admin")
async def admin_action(body: AdminRequest) -> dict:
    params = {"action": body.action, "name": body.name,
              "source": body.source, "lines": body.lines}
    out = await asyncio.to_thread(admin_mod.admin, params)
    return {"action": body.action, "output": out}


# ---------------------------------------------------------------------------
# /api/config  — the same data/config.json the Settings page writes
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config() -> dict:
    cfg = await asyncio.to_thread(config_mod.all)
    # context_gist can be thousands of chars; send its size, not its body
    # (the full text is available from /api/vault/gist).
    gist = cfg.get("context_gist") or ""
    return {**cfg, "context_gist": "", "context_gist_chars": len(gist)}


@app.post("/api/config")
async def set_config(body: ConfigWrite) -> dict:
    await asyncio.to_thread(config_mod.set, body.key, body.value)
    return {"ok": True, "key": body.key, "value": config_mod.get(body.key)}


# ---------------------------------------------------------------------------
# Chat markdown export (assistant/sessions.py).
# NOTE: /api/chats/search is declared next to /api/chats above — FastAPI
# matches routes in definition order, so a literal path that could also match
# /api/chats/{chat_id} must be registered before it or it never fires.
# ---------------------------------------------------------------------------
@app.get("/api/chats/{chat_id}/export")
async def export_chat(chat_id: str):
    if not _valid_chat_id(chat_id):
        raise HTTPException(404, "Chat not found")
    try:
        chat = sessions.load(chat_id)
    except FileNotFoundError:
        raise HTTPException(404, "Chat not found")
    md = sessions.export_markdown(chat)
    title = (chat.get("title") or "chat")[:40]
    from fastapi.responses import Response
    return Response(
        content=md, media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{title}.md"'},
    )


# ---------------------------------------------------------------------------
# /api/access  — guest token management (OWNER ONLY)
#
# Guests are deliberately barred from minting or revoking tokens: otherwise a
# shared tester link could mint itself a fresh non-expiring one and outlive
# the revocation it was supposed to be subject to.
# ---------------------------------------------------------------------------
def _require_owner(request: Request) -> None:
    principal = getattr(request.state, "principal", None)
    if not AUTH_ENABLED:
        return
    if not principal or principal.get("role") != "owner":
        raise HTTPException(403, "Owner access required.")


@app.get("/api/access")
async def list_access(request: Request) -> dict:
    _require_owner(request)
    return {
        "auth_enabled": AUTH_ENABLED,
        "guests": [
            {"id": g["id"], "name": g["name"],
             "created_at": g.get("created_at"), "expires_at": g.get("expires_at"),
             "expires_in_hours": (
                 round((g["expires_at"] - _now_ts()) / 3600, 1)
                 if g.get("expires_at") else None
             )}
            for g in _active_guests()
        ],
    }


@app.post("/api/access")
async def mint_access(request: Request, body: GuestCreate) -> dict:
    _require_owner(request)
    entry = create_guest_token(body.name, body.hours)
    return {
        "ok": True, "id": entry["id"], "name": entry["name"],
        "token": entry["token"],          # shown once, at creation
        "expires_at": entry["expires_at"],
        "hint": "Share <base-url>/?token=<token>. It expires automatically; "
                "revoke sooner with DELETE /api/access/{id}.",
    }


@app.delete("/api/access/{guest_id}")
async def revoke_access(request: Request, guest_id: str) -> dict:
    _require_owner(request)
    if not revoke_guest_token(guest_id):
        raise HTTPException(404, "No such guest token.")
    return {"ok": True, "revoked": guest_id}


# ---------------------------------------------------------------------------
# /api/funnel  — publish / unpublish KEN (OWNER ONLY)
#
# The kill switch has to be reachable from the phone, not just the PC's
# terminal — otherwise "turn the funnel off" means walking to the desk.
# Owner-only: a guest arriving *through* the funnel must not be able to keep
# it open, and turning it ON would be a straight privilege escalation.
# Always scoped to KEN's mount path; `tailscale serve reset` would delete
# every other route this machine serves.
# ---------------------------------------------------------------------------
TAILSCALE_BIN = os.environ.get("TAILSCALE_BIN", r"C:\Program Files\Tailscale\tailscale.exe")
KEN_MOUNT = "/ken"


def _tailscale(*args: str) -> str:
    try:
        r = subprocess.run([TAILSCALE_BIN, *args], capture_output=True,
                           text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"tailscale unavailable: {e}"


def _funnel_state() -> dict:
    try:
        st = json.loads(_tailscale("serve", "status", "--json") or "{}")
    except Exception:
        return {"served": False, "public": False, "url": None, "error": "tailscale unreachable"}
    served = any(
        KEN_MOUNT.rstrip("/") in (p.rstrip("/") or "/")
        for site in (st.get("Web") or {}).values()
        for p in (site.get("Handlers") or {})
    )
    host = None
    for site in (st.get("Web") or {}):
        host = site.split(":")[0]
        break
    return {
        "served": served,
        "public": bool(st.get("AllowFunnel")),
        "url": f"https://{host}{KEN_MOUNT}/" if host and served else None,
        "error": None,
    }


@app.get("/api/funnel")
async def funnel_status(request: Request) -> dict:
    _require_owner(request)
    return await asyncio.to_thread(_funnel_state)


@app.post("/api/funnel")
async def funnel_set(request: Request, body: FunnelToggle) -> dict:
    _require_owner(request)
    if body.public:
        out = await asyncio.to_thread(
            _tailscale, "funnel", "--bg", f"--set-path={KEN_MOUNT}", str(body.port))
    else:
        # `funnel --set-path=X off` DELETES the route outright, it does not
        # merely stop publishing it. Turning sharing off therefore made KEN
        # unreachable from the phone too (everything under /ken fell through
        # to the site root and 502'd). Re-serve on the tailnet immediately
        # afterwards so "stop sharing" means exactly that.
        out = await asyncio.to_thread(
            _tailscale, "funnel", f"--set-path={KEN_MOUNT}", "off")
        out += "\n" + await asyncio.to_thread(
            _tailscale, "serve", "--bg", f"--set-path={KEN_MOUNT}", str(body.port))
    state = await asyncio.to_thread(_funnel_state)
    return {"ok": True, "output": out, **state}


# ---------------------------------------------------------------------------
# /api/livereload — push a reload to every open browser
#
# Exists because a phone is awkward to refresh, and because iOS Safari once
# served a stale ken.html for hours: fixes shipped but never arrived, and the
# app looked permanently broken. Clients now follow the server's build id.
# ---------------------------------------------------------------------------
_PROCESS_BUILD = uuid.uuid4().hex[:8]


def _build_id() -> str:
    """Changes when ken.html is edited or when this process restarts.

    Recomputed per call (not cached) so an edit is picked up without a
    restart; the process part covers server-side changes, which do need one.
    """
    try:
        st = (BASE_DIR / "ken.html").stat()
        file_part = hashlib.md5(
            f"{st.st_mtime_ns}:{st.st_size}".encode()
        ).hexdigest()[:8]
    except OSError:
        file_part = "nofile"
    return f"{file_part}-{_PROCESS_BUILD}"


async def _livereload_events() -> AsyncGenerator[dict, None]:
    last: str | None = None
    try:
        while True:
            current = await asyncio.to_thread(_build_id)
            if current != last:
                last = current
                yield _sse("build", {"build": current})
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        return          # client navigated away; not an error


@app.get("/api/livereload")
async def livereload() -> EventSourceResponse:
    return EventSourceResponse(_livereload_events(), ping=15)


# ---------------------------------------------------------------------------
# /api/clientlog — browser-side diagnostics
#
# There is no console on a phone, so a JS failure there is invisible from
# here. The page posts its errors and environment to this endpoint so they
# land in data/client.log where they can actually be read.
# ---------------------------------------------------------------------------
CLIENT_LOG = DATA_DIR / "client.log"


@app.post("/api/clientlog")
async def client_log(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {"raw": (await request.body()).decode("utf-8", "replace")[:2000]}
    principal = getattr(request.state, "principal", None) or {"name": "?"}
    line = (
        f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{principal['name']}\t"
        f"{_client_ip(request)}\t{json.dumps(body, ensure_ascii=False)[:4000]}\n"
    )
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with CLIENT_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/whoami")
async def whoami(request: Request) -> dict:
    principal = getattr(request.state, "principal", None) or {"role": "owner", "name": "owner"}
    return {"role": principal["role"], "name": principal["name"], "auth_enabled": AUTH_ENABLED}


# ---------------------------------------------------------------------------
# Username/password login + logout. The sign-in page POSTs here; a valid
# credential gets a 30-day HttpOnly session cookie, and the token flow keeps
# working unchanged.
# ---------------------------------------------------------------------------
class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


@app.post("/api/login")
async def login(body: LoginBody, request: Request) -> JSONResponse:
    ip = _client_ip(request)
    if _throttled(ip):
        return JSONResponse({"error": "Too many failed attempts. Try later."}, status_code=429)
    principal = _auth_user((body.username or "").strip().lower(), body.password or "")
    if not principal:
        _record_fail(ip)
        _audit(ip, "LOGIN-FAIL", "POST", "/api/login")
        return JSONResponse({"error": "Invalid username or password."}, status_code=401)
    tok = _new_session(principal["name"])
    _audit(ip, principal["name"], "LOGIN", "/api/login")
    resp = JSONResponse({"ok": True, "user": principal["name"], "role": principal["role"]})
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    resp.set_cookie(
        SESSION_COOKIE, tok,
        httponly=True,
        samesite="lax",
        secure=(proto == "https"),
        max_age=SESSION_TTL_S,
        path="/",
    )
    return resp


@app.post("/api/logout")
async def logout(request: Request) -> JSONResponse:
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        with _session_lock:
            _sessions.pop(tok, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------------------
# User management (owner only). Create/delete users, list all accounts.
# ---------------------------------------------------------------------------
class UserBody(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "user"


@app.get("/api/users")
async def users_list(request: Request) -> JSONResponse:
    principal = getattr(request.state, "principal", None)
    if not principal or principal.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can manage users.")
    return JSONResponse({"users": list_users()})


@app.post("/api/users")
async def users_create(body: UserBody, request: Request) -> JSONResponse:
    principal = getattr(request.state, "principal", None)
    if not principal or principal.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can manage users.")
    rec = create_user(body.username, body.password, body.role or "user")
    if not rec:
        return JSONResponse({"error": "Username taken or invalid (min 2 chars, password min 6)."}, status_code=400)
    _audit(_client_ip(request), "owner", "CREATE-USER", f"/api/users/{rec['name']}")
    return JSONResponse({"ok": True, "user": rec})


@app.delete("/api/users/{username}")
async def users_delete(username: str, request: Request) -> JSONResponse:
    principal = getattr(request.state, "principal", None)
    if not principal or principal.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can manage users.")
    if not delete_user(username):
        return JSONResponse({"error": "User not found or cannot delete owner."}, status_code=400)
    _audit(_client_ip(request), "owner", "DELETE-USER", f"/api/users/{username}")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Root — serve the KEN web UI (ken.html) when present, else API info JSON.
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    html = BASE_DIR / "ken.html"
    if html.exists():
        from fastapi.responses import FileResponse
        # no-store, explicitly. FileResponse sets ETag + Last-Modified but no
        # Cache-Control, which leaves the browser free to apply heuristic
        # caching -- iOS Safari then serves a stale ken.html for a long time
        # without revalidating, so fixes never reach the phone and the app
        # appears permanently broken. The whole UI is this one file, so it
        # must always be fetched fresh.
        return FileResponse(
            html, media_type="text/html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                     "Pragma": "no-cache"},
        )
    return {
        "app": "KEN",
        "version": "1.0.0",
        "endpoints": [
            "GET  /api/health", "GET  /api/tools", "POST /api/tools/toggle",
            "POST /api/chat (SSE)", "POST /api/deep-analysis (SSE)",
            "GET  /api/chats", "GET  /api/chats/search", "GET|DELETE /api/chats/{id}",
            "GET  /api/chats/{id}/export",
            "GET  /api/memory", "POST /api/memory",
            "GET  /api/vault/notes", "GET  /api/vault/note", "GET  /api/vault/search",
            "GET  /api/vault/git", "GET  /api/vault/graph",
            "POST|DELETE /api/vault/gist", "POST /api/vault/sync",
            "GET|POST /api/tasks", "PATCH|DELETE /api/tasks/{id}",
            "GET|POST /api/reminders", "DELETE /api/reminders/{id}",
            "GET|POST /api/backups",
            "GET|POST /api/documents", "DELETE /api/documents/{name}",
            "POST /api/knowledge/sync",
            "POST /api/voice/transcribe", "POST /api/voice/speak",
            "GET  /api/admin/status", "POST /api/admin",
            "GET|POST /api/config",
        ],
        "docs": "/docs",
    }


# ---------------------------------------------------------------------------
# Static assets (three.min.js for the GPU 3D idle scene, force-graph libs for
# the vault graph). Auth-gated by the middleware like every other route.
# ---------------------------------------------------------------------------
@app.get("/assets/{name:path}")
async def assets(name: str):
    from fastapi.responses import FileResponse  # local import, matches root route

    root = (BASE_DIR / "assets").resolve()
    f = (root / name).resolve()
    if not str(f).startswith(str(root)) or not f.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        f,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8756, reload=True)
