# Architecture - KEN personal assistant

## Processes

| Process | Entry point | Notes |
|---|---|---|
| KEN server (FastAPI + SSE) | `server.py` (`uvicorn server:app`, `127.0.0.1:8756`) | serves `ken.html` and `/api/*` |
| One-command launcher | `app.py` | starts the server and opens the browser |
| Headless service | `ken_service.py` (`pythonw.exe`) | supervisor loop that restarts uvicorn; registered as a logon task by `register_ken_task.ps1` / `register_ken_boot_task.ps1` |
| Sandbox | `sandbox.py` | isolated copy of the server on a test port (:8766 per `qa/BOT_RULES.md`) |
| Desktop app | `desktop/main.js` (Electron 33) | LOCAL mode starts `ken_service.py` and injects the owner token; REMOTE mode (`KEN_URL` / `KEN_REMOTE=1`) loads a remote KEN and the user signs in |
| Sharing | `ken_share.py` | mint/revoke tester links, run `tailscale funnel` |
| Reminder daemon (optional) | `assistant/reminder_daemon.py --install` | Windows toast at due time |
| Fleet bots | `qa/*.py` | scheduled externally (Hermes/Ken cron, Windows tasks) |

## `assistant/` package (wrapped, not modified, by the server)

`config.py` (central `data/config.json` + defaults), `llm.py` (system prompt + tool loop),
`local_llm.py` (LM Studio OpenAI-compatible transport, `http://127.0.0.1:1234/v1`; Ollama
constant kept as legacy), `tools.py` (registry + schemas), `memory.py`, `observations.py`,
`distill.py`, `rag.py` (BM25 + cosine hybrid), `sessions.py` (JSONL chats), `compaction.py`,
`repl.py`/`repl_worker.py` (persistent Python), `crew.py`/`crew_tools.py`/`crew_cache.py`
(CrewAI deep analysis), `vault.py`/`vault_graph.py`/`graph.py` (Obsidian vault + graph),
`mcp.py` (stdio MCP client), `skills.py`, `todo.py`, `reminders.py`, `backup.py`,
`voice.py` (faster-whisper / pyttsx3), `orchestra.py`, `admin.py`, `deepseek_key.py`.

## Chat request flow (`server.py`)

1. Auth middleware: owner/guest token (cookie `ken_token`, tokens in `data/.ken_tokens.json`)
   or username/password session (`data/.ken_users.json`, scrypt); per-IP global rate guard.
2. `POST /api/chat` -> worker thread runs an OpenAI-style tool-calling loop against DeepSeek
   (`KEN_MODEL`, default `deepseek-chat`) with the same system prompt and tool schemas as
   `assistant/llm.py`; only enabled tools are exposed; tool timings stream as SSE events.
3. If DeepSeek is unusable at turn start, retry once on the local loop (`assistant/llm.py` ->
   `local_llm`); free cloud relays (Bluesminds, Groq, OpenRouter) are also configured.
4. Turns persist via `sessions.py`; tool calls append to `data/observations.jsonl`.
5. Optional A2A bridge to Hermes/Ken using `A2A_BEARER_TOKEN` (`KEN_HERMES_A2A`).

## Fleet runtime (`qa/`)

`bot_runner.py` (runs bots from `data/bots.json`, state in `data/bots_state.json`, approvals in
`data/approvals.jsonl`), `run_500agent.py` (runs agents from `AGENTS_500_REPO`, default `E:/Local/projects/500-AI-Agents-Projects`, with that repo's `.venv`), `work_queue.py`
(SQLite queue with idempotency keys and retries), `event_bus.py` (SQLite events),
`fleet_common.py` (checkpoints/idempotency), `fleet_boss.py`, `qa_runner.py` (deterministic
hourly QA), `provider_health.py`, `external_watchdog.py`, `game_mode*.py` (pause background
work while a game runs), `screen_catcher.py`, `log_aggregate.py`, `memory_distill.py`,
Playwright checks (`qa/playwright.config.js`).

## External services

DeepSeek API, Bluesminds, Groq, OpenRouter, LM Studio (local), DuckDuckGo, Open-Meteo,
Wikipedia, Claude Code CLI (deep-analysis step, with local fallback per `README.md`),
Graphiti knowledge graph (`graphiti_mcp_server.py`, `ingest_vault.py`), Tailscale
(serve/funnel), a WhatsApp bridge helper pair in `qa/` (`wa_pair.py`, `qr_watch.py`).

## Verified against

`server.py` docstring and lines 75-300, 400-460, 960-980, `app.py`, `ken_service.py`,
`sandbox.py`, `ken_share.py`, `desktop/main.js`, `desktop/package.json`, `assistant/*.py`
file list, `assistant/config.py`, `assistant/local_llm.py`, `qa/*.py` docstrings,
`qa/BOT_RULES.md`, `requirements.txt`, `requirements-server.txt`.
