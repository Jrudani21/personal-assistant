# KEN — Addendum: use the DeepSeek hosted API instead of local Ollama

> Paste this to DeepSeek AFTER it has produced `server.py` from `KEN_BUILD_SPEC.md`.
> It changes ONLY the model provider. Endpoints, SSE event names, tool execution,
> chat/memory persistence, and the whole contract in `API.md` stay identical — the
> frontend must not need any change.

## What changes

The assistant currently calls a local Ollama server. Switch the CHAT COMPLETION
calls to DeepSeek's OpenAI-compatible API. Tool execution, memory, RAG, and
persistence all stay local and unchanged — only the text-generation calls move
to DeepSeek's cloud.

## Provider config (env vars)

- `DEEPSEEK_API_KEY` — required. The server must refuse to start (clear error)
  if it is missing.
- `DEEPSEEK_BASE_URL` — default `https://api.deepseek.com/v1`
- `KEN_MODEL` — default `deepseek-chat` (DeepSeek V3.x/V4 chat model).
  Optionally support `deepseek-reasoner` as an alternate.
- Keep `KEN_FALLBACK_MODEL` (default `qwen3:8b` via local Ollama) as an OPTIONAL
  offline fallback: if the DeepSeek API call fails before anything has streamed
  (network down, 401, 429), retry once against local Ollama exactly as before.
  `done.model` still reports which model actually answered.

## Implementation requirements

1. Use the DeepSeek API in OpenAI-compatible mode. Either the `openai` Python
   SDK pointed at `base_url=DEEPSEEK_BASE_URL, api_key=DEEPSEEK_API_KEY`, or a
   plain `requests`/`httpx` POST to `{BASE_URL}/chat/completions`.
2. STREAM the completion (`stream: true`) and translate DeepSeek's streamed
   `choices[].delta.content` chunks into the SAME `event: token / data:{"delta": ...}`
   SSE the frontend already consumes. Do not change the event shape.
3. TOOL CALLING: DeepSeek's API supports OpenAI-style `tools` + `tool_calls`.
   Pass the enabled subset of `SCHEMAS` as the `tools` param. When the model
   returns `tool_calls`, execute them via the local `REGISTRY` (emitting
   `tool_start` / `tool_end` exactly as now), append the tool results as
   `role:"tool"` messages, and continue the completion — same loop shape as the
   Ollama tool loop, just against DeepSeek.
4. `/api/health` -> `{status, model, base_url, provider:"deepseek", ollama_up}`
   where `ollama_up` reflects the local fallback's availability.
5. `/api/deep-analysis`: the CrewAI analyze step should also target DeepSeek
   (set its model/creds), keeping the local fallback path.
6. Never log or echo the API key. Read it once at startup.
7. requirements: add `openai` (or `httpx`) as needed.

## Privacy note to surface

This makes chat NON-local: prompts and tool inputs are sent to DeepSeek's
servers. Only the LLM calls — file contents, memory values, and RAG chunks are
still sent whenever they are included in a prompt. If any workspace data must
never leave the machine, keep those chats on the local `KEN_FALLBACK_MODEL`
instead.

## Run

```
# Windows PowerShell
$env:DEEPSEEK_API_KEY="sk-..."
$env:KEN_MODEL="deepseek-chat"
py -3.12 -m uvicorn server:app --port 8756 --reload

# Mac/Linux
DEEPSEEK_API_KEY=sk-... KEN_MODEL=deepseek-chat uvicorn server:app --port 8756 --reload
```

Deliver the updated `server.py` in full and a one-paragraph diff summary of what
changed vs the Ollama version.
