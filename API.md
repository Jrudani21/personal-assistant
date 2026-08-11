# KEN — HTTP + SSE API

Thin FastAPI backend wrapping the existing `assistant` package. Base URL:
`http://localhost:8756`.

- All requests/responses are JSON unless noted (SSE endpoints stream
  `text/event-stream`).
- **Chat is NOT local by default.** The primary model provider is
  DeepSeek's hosted API (`https://api.deepseek.com`) — prompts, tool
  inputs, and any memory/RAG content included in a prompt are sent there.
  Tool execution, memory, RAG, and persistence all stay on this machine;
  only the text-generation calls leave it. See
  `KEN_ADDENDUM_deepseek_api.md`. `deep_analysis`'s Analyst stage
  (`assistant/crew.py`) also targets DeepSeek.
- The server refuses to start if `DEEPSEEK_API_KEY` is not set.
- Model: `KEN_MODEL` env var, default `deepseek-chat`. Base URL:
  `DEEPSEEK_BASE_URL`, default `https://api.deepseek.com`. If DeepSeek is
  unusable from the start of a turn (network down, 401, 429, ...),
  `/api/chat` automatically retries once against the free local
  `KEN_FALLBACK_MODEL` (default `qwen3:8b`, via Ollama — fully on-machine).
- CORS allows `http://localhost:*` and `file://` (browsers send
  `Origin: null` for `file://` pages — that origin is allowed too).
- Interactive docs: `http://localhost:8756/docs`.

---

## 1. Health

### `GET /api/health`

```json
{ "status": "ok", "model": "deepseek-chat", "provider": "deepseek", "base_url": "https://api.deepseek.com", "fallback_model": "qwen3:8b", "ollama_up": true }
```

- `ollama_up` reflects the *fallback* path's availability (local Ollama),
  checked with a 2 s timeout — DeepSeek being down doesn't affect it.
- If both DeepSeek and local Ollama are unreachable, `/api/chat` emits a
  single `error` event instead of crashing.

> Model note: the primary provider is DeepSeek's hosted API — pay-per-token,
> requires `DEEPSEEK_API_KEY`. Chats automatically fall back to the free
> local `KEN_FALLBACK_MODEL` (`qwen3:8b`) when DeepSeek is unusable from the
> start of a turn — `done.model` tells you which model actually answered.
> Set `KEN_MODEL`/`DEEPSEEK_BASE_URL`/`KEN_FALLBACK_MODEL` to override.

---

## 2. Tools

### `GET /api/tools`

Array of every tool in the assistant's real `REGISTRY`:

```json
[
  { "name": "calculator",    "group": "core",      "enabled": true,  "description": "Evaluate a numeric arithmetic expression (+ - * / ** % //)." },
  { "name": "write_file",    "group": "workspace", "enabled": false, "description": "Write/overwrite a text file in the assistant's local workspace folder." },
  { "name": "deep_analysis", "group": "agents",    "enabled": true,  "description": "Run a 4-step research pipeline ..." }
]
```

- `group` — one of `core | workspace | knowledge | memory | planning | agents | other`
  (sidebar grouping for the UI).
- `enabled` — persisted in `data/config.json`; destructive tools
  (`write_file`, `run_python`, `restart_python_session`, `forget`,
  `clear_tasks`, `cancel_reminder`, `clear_crew_cache`) default to `false`,
  everything else to `true`.

### `POST /api/tools/toggle`

Body:

```json
{ "name": "write_file", "enabled": true }
```

Returns the updated tool (same shape as above). Persists to
`data/config.json`. `404` for an unknown tool name.

---

## 3. Chat (streaming, SSE)

### `POST /api/chat`

Body:

```json
{
  "messages": [ { "role": "user", "content": "What's the weather in Winnipeg?" } ],
  "enabled_tools": ["calculator", "web_search", "weather"],
  "chat_id": "7afc50d5231a"
}
```

- `messages` — prior turns are optional; the **last message must have
  `role: "user"`** (400 otherwise). The system prompt (persona + 30 recent
  memory entries + due reminders) is assembled internally by `assistant/llm.py`.
- `enabled_tools` — only these tools are *offered* to the model (schema
  filtering) and *allowed* to execute. A disabled/unknown tool the model
  still tries returns a tool result `"Tool unavailable: <name> is not in
  this request's enabled_tools."` and is never executed.
- `chat_id` — optional. When omitted (or unknown), a new chat is created
  and its id returned in the `done` event.

Response: `text/event-stream`. Events (one JSON object per `data:` line):

| event       | data                                                        |
|-------------|-------------------------------------------------------------|
| `token`     | `{ "delta": "partial assistant text" }`                     |
| `tool_start`| `{ "name": "weather", "args": { "location": "Winnipeg" } }` |
| `tool_end`  | `{ "name": "weather", "ms": 812, "ok": true, "summary": "Winnipeg, Canada: 21C, humidity 55%, wind 12 km/h" }` |
| `error`     | `{ "message": "..." }`                                      |
| `done`      | `{ "turn_id": "ab12cd34ef56", "chat_id": "7afc50d5231a", "model": "deepseek-chat" }` |

Example stream:

```
event: token
data: {"delta": "Let me "}

event: tool_start
data: {"name": "weather", "args": {"location": "Winnipeg"}}

event: tool_end
data: {"name": "weather", "ms": 812, "ok": true, "summary": "Winnipeg, Canada: 21C, humidity 55%, wind 12 km/h"}

event: token
data: {"delta": "It's 21°C in Winnipeg with 55% humidity."}

event: done
data: {"turn_id": "ab12cd34ef56", "chat_id": "7afc50d5231a", "model": "qwen3:8b"}
```

Behavior notes:

- `token` events arrive in order; concatenating the `delta`s gives the full
  assistant reply. Tool calls execute synchronously *between* token events
  (the model produces no text while calling tools).
- The turn (user message + final assistant reply) is persisted via
  `assistant/sessions.py`; every tool call is logged to
  `data/observations.jsonl` by the assistant itself.
- `ms` is real wall-clock time (`time.perf_counter` around the tool call).
- **Model fallback**: if DeepSeek is unusable from the start of a turn (no
  key, network down, 401, 429, ...), the turn is silently retried once with
  `fallback_model` (`qwen3:8b` via local Ollama, by default). No `error`
  event is emitted for that retry — the stream just proceeds with the
  fallback, and `done.model` reports which model actually answered. A
  fallback is only attempted when nothing has streamed yet; a mid-stream
  failure surfaces as an `error`/error-text event as normal.
- `error` is terminal; `done` is terminal. If both DeepSeek and the local
  fallback are down, exactly one `error` event is emitted (no 500).
- The stream sends a keepalive comment (`: ping`) every 15 s.

---

## 4. Deep analysis (streaming, SSE)

### `POST /api/deep-analysis`

Body:

```json
{ "question": "Compare the revenue per unit of Product A vs Product B" }
```

Runs the real 4-agent crew (`assistant/crew.py`): fetch → verify/compute →
analyze → report, with numeric-consistency guardrails and a 7-day cache.
Takes ~45–120 s on a fresh run; cached runs return instantly.

Events:

| event   | data |
|---------|------|
| `stage` | `{ "stage": "fetch", "status": "running", "ms": 0, "note": "starting deep-analysis pipeline" }` |
| `stage` | `{ "stage": "report", "status": "done", "ms": 71234, "note": "" }` |
| `done`  | `{ "findings": "<full report text>", "sources": [], "guardrail": { "numeric": "pass", "coverage": "3/3", "local_fallback": false }, "cached": false }` |
| `error` | `{ "message": "..." }` |

Example stream (fresh run):

```
event: stage
data: {"stage": "fetch", "status": "running", "ms": 0, "note": "starting deep-analysis pipeline"}

event: stage
data: {"stage": "report", "status": "done", "ms": 71234, "note": ""}

event: done
data: {"findings": "Product A earns $4.10/unit vs Product B's $3.20 ...", "sources": [], "guardrail": {"numeric": "pass", "coverage": "3/3", "local_fallback": false}, "cached": false}
```

Example stream (cache hit — instant):

```
event: done
data: {"findings": "<cached report>", "sources": [], "guardrail": {"numeric": "pass", "coverage": "3/3", "local_fallback": false}, "cached": true}
```

Behavior notes:

- `findings` is the report **string** (the assistant's `deep_analysis` tool
  also returns a string). The build spec sketched an array; a string is what
  the wrapped module produces — consumers should render it as text.
- Per-task (`fetch`/`verify`/`analyze`) stage granularity is **not** exposed
  by `assistant/crew.py` without modifying it, so the server emits honest
  coarse progress (`fetch` running → `report` done) instead of fabricating
  per-stage timings. Consumers should treat `stage` events as
  informational progress, not a strict 4-step contract.
- `guardrail.local_fallback` is `true` when the DeepSeek analysis step was
  unavailable and the crew fell back to a local model (the report text then
  carries the assistant's standard fallback warning).
- If `crewai` isn't installed, a single `error` event is emitted.

---

## 5. Chats

### `GET /api/chats`

Newest first:

```json
[
  { "id": "7afc50d5231a", "title": "Weather in Winnipeg", "updated_at": "2026-08-11T09:12:44", "message_count": 3 },
  { "id": "bd1233a106b1", "title": "New chat",           "updated_at": "",                       "message_count": 0 }
]
```

### `GET /api/chats/{id}`

```json
{
  "id": "7afc50d5231a",
  "title": "Weather in Winnipeg",
  "messages": [
    { "role": "user",      "content": "What's the weather in Winnipeg?" },
    { "role": "assistant", "content": "It's 21°C in Winnipeg with 55% humidity." }
  ]
}
```

`404` for an unknown id.

### `DELETE /api/chats/{id}`

```json
{ "ok": true }
```

`404` for an unknown id.

---

## 6. Memory

### `GET /api/memory`

Most recent first:

```json
[
  { "key": "favorite_language", "value": "Python", "facts": ["Uses uv for env management"], "concepts": ["preferences", "dev"], "updated_at": "2026-08-10T18:02:11.123456" }
]
```

### `POST /api/memory`

Body:

```json
{ "key": "favorite_language", "value": "Python", "facts": ["Uses uv for env management"], "concepts": ["preferences", "dev"] }
```

`facts` and `concepts` are optional arrays.

Response:

```json
{ "ok": true, "message": "Remembered 'favorite_language'.", "entry": { "key": "favorite_language", "value": "Python", "facts": ["Uses uv for env management"], "concepts": ["preferences", "dev"], "updated_at": "2026-08-10T18:02:11.123456" } }
```

**Never overwrites**: `409 Conflict` if the key already exists (the
assistant's `remember` tool *does* overwrite — the HTTP API is intentionally
stricter per the build spec).

---

## 7. Root

### `GET /`

```json
{ "app": "KEN", "version": "1.0.0", "endpoints": [ "..." ], "docs": "/docs" }
```

---

## Implementation notes (for maintainers)

- **Model provider**: `assistant/llm.py`'s `stream_chat` is hardcoded to
  `ollama.chat` and is off-limits to edit (build spec: import/wrap, never
  modify `assistant/*`). So the primary DeepSeek path is a second,
  equivalent OpenAI-style tool-calling loop implemented in `server.py`
  (`_deepseek_stream_chat`) — same system prompt (reuses
  `llm._system_prompt()`), same `tools.REGISTRY`/`llm.SCHEMAS`, same
  observation logging, only the transport differs. The fallback path still
  calls `assistant/llm.py`'s real loop against Ollama, unchanged.
- **Threading model**: both loops are synchronous. `/api/chat`
  and `/api/deep-analysis` run them in a daemon worker thread and bridge
  events to the async SSE generator through a `queue.Queue`
  (`asyncio.to_thread` on `queue.get` and on `thread.join`). The event loop
  is never blocked.
- **chat_id validation**: ids are always `uuid.uuid4().hex[:12]`
  (12 lowercase hex chars). `POST /api/chat`'s `chat_id` field and the
  `GET`/`DELETE /api/chats/{id}` path param are validated against that
  format before reaching `assistant/sessions.py`, which builds file paths
  by straight string interpolation — an unvalidated id would be a path-
  traversal vector, and CORS here allows any `localhost` origin plus
  `file://`, so any local page can call this API.
- **Tool filtering** is enforced by temporarily (for the turn only) replacing
  `llm.SCHEMAS` with the enabled subset and wrapping `REGISTRY` entries —
  enabled tools get a timing wrapper (emits `tool_start`/`tool_end`), disabled
  ones a stub that returns "Tool unavailable…" without executing. Both are
  restored in a `finally` block; a module lock serializes turns.
- **Config**: tool on/off overrides live in `data/config.json`
  (`{ "tools": { "<name>": bool } }`). There is no `assistant/config.py`
  module — the spec listed one, but it doesn't exist, so the server owns
  `data/config.json` directly.
- **Name corrections vs the build spec**: the spec's `sync_knowledge` is
  `sync_vault` in the real REGISTRY, and `query_sql`, `transcribe_file`,
  `mcp_*`, `list_skills`, `run_skill`, `fetch_webpage` don't exist — they are
  omitted, not fabricated.
- **Python**: the assistant requires Python 3.12+ (f-strings with backslashes
  in `assistant/llm.py`). Run with the 3.12 interpreter:
  `py -3.12 -m uvicorn server:app --port 8756 --reload`.
