# KEN — Build Spec for DeepSeek V4

> Machine-readable handoff. Paste this whole file into DeepSeek as the first message.
> Goal: replace Streamlit with a local web frontend ("KEN") for an existing Python
> personal-assistant, plus a thin FastAPI backend that wraps the assistant's existing
> logic. DO NOT rewrite the assistant. IMPORT and wrap it. Everything runs locally.

---

## 0. ROLE

You are a senior backend engineer. You will:
1. Read the existing modules listed in section 2 (confirm exact symbol names first).
2. Build `server.py` (FastAPI) that exposes the assistant over HTTP + SSE.
3. Build `API.md` documenting every endpoint and SSE event with example payloads.
4. Not modify any existing `assistant/*` file. Only import from them.

Output `server.py` in full first, then `API.md`.

---

## 1. ENVIRONMENT

- Language: Python 3.11+
- Model: `deepseek-v4` served by local Ollama at `http://localhost:11434`
- Assistant repo root contains: `app.py` (Streamlit, being replaced), `assistant/` package,
  `pages/1_Settings.py`, `data/` (memory.json, chats, observations, embeddings, config.json)
- New files you create: `server.py`, `API.md`, `requirements-server.txt`
- Run command target: `uvicorn server:app --port 8756 --reload`
- All data stays on machine. No network calls except to local Ollama.

---

## 2. EXISTING MODULES — IMPORT, DO NOT EDIT

| Module | What it provides |
|---|---|
| `assistant/llm.py` | Ollama chat + tool-calling loop; system-prompt assembly |
| `assistant/tools.py` | `REGISTRY` (name -> callable), `SCHEMAS` (OpenAI-style function schemas) |
| `assistant/memory.py` | Structured JSON memory (value + facts[] + concepts[] + updated_at). Inject 30 most recent into system prompt |
| `assistant/sessions.py` | Per-chat persistence (append-only JSONL + `.meta.json` sidecar), search, Markdown export |
| `assistant/rag.py` | Document ingest + hybrid BM25+cosine search |
| `assistant/crew.py` | CrewAI 4-agent deep-analysis: fetch -> verify -> analyze -> report; numeric-consistency guardrail; 7-day cache |
| `assistant/config.py` | Central JSON config at `data/config.json`; the Settings page writes here |
| `assistant/observations.py` | Append-only JSONL log of every tool call |
| `assistant/distill.py` | Memory distillation from observations (new keys only, never overwrite) |
| `assistant/todo.py` | JSON to-do store |
| `assistant/reminders.py` | JSON reminder store |

FIRST STEP: open `tools.py` and confirm the real names/signatures of `REGISTRY` and
`SCHEMAS` and how a tool is invoked (positional vs kwargs). If a name differs, adapt.
Do the same for the chat-loop entrypoint in `llm.py`.

---

## 3. TOOL PERMISSION MODEL

These tools are DESTRUCTIVE — default OFF, only callable when explicitly enabled via toggle:
`write_file`, `run_python`, `restart_python_session`, `forget`, `clear_tasks`,
`cancel_reminder`, `clear_crew_cache`.

All others default ON. The model must only ever be offered / allowed to call tools whose
name is in the request's `enabled_tools` list. If the model tries to call a disabled or
unknown tool, do not execute it — return a tool result saying the tool is unavailable.

Tool groups (for the UI sidebar), by name:
- core: calculator, get_datetime, web_search, weather, wikipedia_summary, fetch_webpage
- workspace: read_file, write_file, list_files, run_python, restart_python_session
- knowledge: search_documents, sync_knowledge, query_sql, transcribe_file
- memory: remember, recall, forget, recent_activity, distill_memory, backup_data
- planning: add_task, list_tasks, complete_task, clear_tasks, remind_me, list_reminders, cancel_reminder
- agents: deep_analysis, clear_crew_cache, mcp_list_tools, mcp_call_tool, list_skills, run_skill

---

## 4. ENDPOINTS

### GET /api/health
-> `{ "status": "ok", "model": "deepseek-v4", "ollama_up": true }`

### GET /api/tools
-> array of `{ "name": str, "group": str, "enabled": bool, "description": str }`
Derive `description` from `SCHEMAS`. `enabled` from config (defaults per section 3).

### POST /api/tools/toggle
body `{ "name": str, "enabled": bool }` -> persist to `data/config.json`, return the updated tool.

### POST /api/chat  (STREAMING, Server-Sent Events)
body:
```json
{ "messages": [{"role":"user","content":"..."}], "enabled_tools": ["calculator","web_search"], "chat_id": "optional" }
```
Behavior: assemble the system prompt (persona + 30 recent memory entries + enabled tool
schemas), run the real tool-calling loop from `llm.py`. Execute tool calls via `REGISTRY`,
filtered by `enabled_tools`. Persist the turn via `sessions.py`. Log tool calls via
`observations.py`.

Emit these SSE event types (one JSON object per `data:` line):
```
event: token       data: {"delta": "partial assistant text"}
event: tool_start  data: {"name": "run_python", "args": {...}}
event: tool_end    data: {"name": "run_python", "ms": 812, "ok": true, "summary": "returned 412"}
event: error       data: {"message": "..."}
event: done        data: {"turn_id": "...", "chat_id": "..."}
```

### POST /api/deep-analysis  (STREAMING SSE)
body `{ "question": str }` -> run `crew.py`. Stream stage progress:
```
event: stage  data: {"stage": "fetch",   "status": "done", "ms": 4100, "note": "pulled 7 logs"}
event: stage  data: {"stage": "verify",  "status": "done", "ms": 2700}
event: stage  data: {"stage": "analyze", "status": "done", "ms": 31000}
event: stage  data: {"stage": "report",  "status": "running"}
event: done   data: {"findings": [...], "sources": [...], "guardrail": {"numeric": "pass", "coverage": "3/3", "local_fallback": false}, "cached": false}
```

### GET /api/chats -> `[{id, title, updated_at, message_count}]`  (from sessions.py)
### GET /api/chats/{id} -> `{id, title, messages:[...]}`
### DELETE /api/chats/{id}
### GET /api/memory -> `[{key, value, facts, concepts, updated_at}]`  (most recent first)
### POST /api/memory -> body `{key, value, facts?, concepts?}` add via memory.py (never overwrite existing key)

---

## 5. TECHNICAL CONSTRAINTS

- CORS: allow origins `http://localhost:*` and `file://` (the KEN HTML is opened as a local file).
- The assistant loop in `llm.py` is synchronous. Do NOT block the event loop:
  run it in a threadpool (`anyio.to_thread.run_sync` or a `ThreadPoolExecutor`) and
  bridge tokens/events to the async SSE generator through a `queue.Queue` or
  `asyncio.Queue` fed from the worker thread.
- Use `sse-starlette`'s `EventSourceResponse` (or hand-rolled `text/event-stream`).
- Report REAL wall-clock `ms` per tool call (time.perf_counter around the REGISTRY call).
- Never expose a tool not in `enabled_tools`.
- Graceful degradation: if Ollama is unreachable, `/api/health` returns `ollama_up:false`
  and `/api/chat` returns a single `error` event, never a 500 crash.

---

## 6. DELIVERABLES

1. `server.py` — full, runnable.
2. `requirements-server.txt` — additions only: `fastapi`, `uvicorn[standard]`, `sse-starlette`, `anyio`.
3. `API.md` — every endpoint + every SSE event type with a concrete example payload.
   (A separate HTML frontend consumes exactly these events — the contract must be exact.)

Begin by printing the confirmed symbol names you found in `tools.py` and `llm.py`,
then `server.py` in full, then `API.md`.
