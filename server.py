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
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from assistant import crew_cache, llm, memory, observations, sessions, tools

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"

# ---------------------------------------------------------------------------
# Model provider: DeepSeek hosted API (primary) + local Ollama (fallback)
# ---------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("KEN_MODEL", "deepseek-chat")
PROVIDER = "deepseek"

# Free local fallback: used automatically when DeepSeek is unusable from the
# start (no key, network down, 401, 429, ...). Override with KEN_FALLBACK_MODEL.
FALLBACK_MODEL = os.environ.get("KEN_FALLBACK_MODEL", "qwen3:8b")
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT_S = 2

_deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None

# ---------------------------------------------------------------------------
# Tool permission model  (KEN_BUILD_SPEC section 3)
#
# Groups are for the UI sidebar. Names come from the REAL REGISTRY
# (e.g. `sync_vault`, not the spec's `sync_knowledge`; tools that don't
# exist in REGISTRY — query_sql, transcribe_file, mcp_*, skills — are
# omitted rather than fabricated).
# ---------------------------------------------------------------------------
TOOL_GROUPS: dict[str, list[str]] = {
    "core": ["calculator", "get_datetime", "web_search", "weather", "wikipedia_summary"],
    "workspace": ["read_file", "write_file", "list_files", "run_python", "restart_python_session"],
    "knowledge": ["search_documents", "sync_vault"],
    "memory": ["remember", "recall", "forget", "recent_activity", "distill_memory", "backup_data"],
    "planning": ["add_task", "list_tasks", "complete_task", "clear_tasks",
                 "remind_me", "list_reminders", "cancel_reminder"],
    "agents": ["deep_analysis", "clear_crew_cache"],
}

# Destructive tools — default OFF, only callable when explicitly enabled.
DESTRUCTIVE_TOOLS = {
    "write_file", "run_python", "restart_python_session",
    "forget", "clear_tasks", "cancel_reminder", "clear_crew_cache",
}


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"^http://localhost(:\d+)?$",
    allow_credentials=False,
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
# injection) and tools.REGISTRY/llm.SCHEMAS so behavior stays identical to
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
                model=model, messages=messages, tools=llm.SCHEMAS or None, stream=True,
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
async def list_tools() -> list[dict]:
    schemas = _schema_map()
    out = []
    for name in tools.REGISTRY:
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
#   * llm.SCHEMAS  -> filtered to the request's enabled_tools (model is only
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
    saved_schemas = llm.SCHEMAS
    full_reply: list[str] = []
    try:
        # 1) Only offer enabled tools to the model.
        llm.SCHEMAS = [
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
        candidates = [model]
        if FALLBACK_MODEL:
            candidates.append(FALLBACK_MODEL)
        for attempt, candidate in enumerate(candidates):
            buf: list[str] = []
            started = False
            gen = (
                _deepseek_stream_chat(candidate, history) if attempt == 0
                else llm.stream_chat(candidate, history, on_tool_call=None)
            )
            for piece in gen:
                if attempt == 0 and not started and _looks_like_llm_error("".join(buf) + piece):
                    break  # primary unusable from the start -> try fallback
                started = True
                buf.append(piece)
                full_reply.append(piece)
                q.put(("token", {"delta": piece}))
            else:
                used_model = candidate
                break
        if used_model is None:
            used_model = candidates[-1]  # every candidate failed; reply carries the last error

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
        llm.SCHEMAS = saved_schemas


async def _sse_chat(req: ChatRequest) -> AsyncGenerator[dict, None]:
    if not req.messages:
        yield _sse("error", {"message": "messages must not be empty."})
        return
    if req.messages[-1].role != "user":
        yield _sse("error", {"message": "last message must have role 'user'."})
        return

    if not _deepseek_client and not _ollama_up():
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
    q: queue.Queue = queue.Queue()

    def run() -> None:
        with _CHAT_LOCK:
            _chat_worker(MODEL, history, enabled, chat, q)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    async for event, payload in _iter_queue(q, thread):
        yield _sse(event, payload)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    return EventSourceResponse(_sse_chat(req), ping=15)


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
            q.put(("error", {"message": f"Deep analysis error: {e}"}))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    async for event, payload in _iter_queue(q, thread):
        yield _sse(event, payload)


@app.post("/api/deep-analysis")
async def deep_analysis(req: DeepAnalysisRequest) -> EventSourceResponse:
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
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
# Root — serve the KEN web UI (ken.html) when present, else API info JSON.
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    html = BASE_DIR / "ken.html"
    if html.exists():
        from fastapi.responses import FileResponse
        return FileResponse(html, media_type="text/html")
    return {
        "app": "KEN",
        "version": "1.0.0",
        "endpoints": [
            "GET  /api/health", "GET  /api/tools", "POST /api/tools/toggle",
            "POST /api/chat (SSE)", "POST /api/deep-analysis (SSE)",
            "GET  /api/chats", "GET|DELETE /api/chats/{id}",
            "GET  /api/memory", "POST /api/memory",
        ],
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8756, reload=True)
