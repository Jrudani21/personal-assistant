# PRD - KEN personal assistant (personal-assistant repo)

> Inferred from `README.md`, `KEN_BUILD_SPEC.md`, `API.md`, `server.py`, `app.py`,
> `desktop/` and `qa/`. **`README.md` is out of date** (it describes a Streamlit app on
> Ollama; the code is a FastAPI server + web UI on DeepSeek with local/free fallbacks).

## What it is

"KEN": the owner's personal AI assistant, served by a local FastAPI backend (`server.py`,
port 8756, loopback only) with a single-file web UI (`ken.html`) and an Electron desktop shell
(`desktop/`). It wraps the `assistant/` package: tool-calling chat, memory, RAG over uploaded
docs and the Obsidian vault, tasks/reminders, a CrewAI deep-analysis pipeline, voice.
The same repo also hosts the **bot fleet runtime** in `qa/` (scheduled QA, provider-health,
ops-boss, work queue, event bus, game mode) whose state feeds the fleet dashboards.

## Users

- The owner (local, auto-injected owner token in the desktop app).
- Invited users / testers: accounts or tester links minted by the owner (`ken_share.py`),
  reached through `tailscale serve` (tailnet) or `tailscale funnel` (public). Remote users
  spend the owner's DeepSeek key (`desktop/main.js` header comment).
- Bots and agents: `qa/BOT_RULES.md` defines what bots may do (sandbox on :8766 = anything;
  live service on :8756 = read-only).

## Capabilities (implemented)

- Streaming chat over SSE with tool calls (`POST /api/chat`), deep analysis
  (`POST /api/deep-analysis`), chats CRUD/search/export, memory, vault browse/search/graph,
  tasks, reminders, backups (`API.md`: 55 route decorators in `server.py`).
- Tools (README list, still accurate for `assistant/tools.py`): calculator, web search
  (DuckDuckGo), weather, Wikipedia, sandboxed file I/O and persistent Python session, RAG,
  structured memory + distillation, tasks, reminders, MCP client, read-only SQL, web fetch,
  transcription, skills.
- Model routing: DeepSeek (`KEN_MODEL`, default `deepseek-chat`) first; on failure, free
  cloud relays (Bluesminds, Groq, OpenRouter) and the local LM Studio tier
  (`http://127.0.0.1:1234/v1`).

## Non-goals (stated)

- Privacy-by-default is no longer a goal for chat: `server.py` states prompts, tool inputs and
  included memory/RAG content are sent to DeepSeek; only the local fallback stays on-machine.
- Not a multi-tenant product; user accounts are owner-managed.

## Open questions

- `README.md` (Streamlit, Ollama, `app.py` = Streamlit UI) and `pages/1_Settings.py` (a
  Streamlit page, while `app.py` no longer starts Streamlit) look orphaned. Retire or update?
  Unknown - needs owner input.
- Resolved: `data/memory.json` (personal profile facts) is local only and not versioned.
- Success criteria: Unknown - needs owner input.

## Verified against

`README.md`, `KEN_BUILD_SPEC.md` headings, `API.md` headings, `server.py` docstring and
config constants, `app.py`, `desktop/package.json`, `desktop/main.js` header,
`qa/BOT_RULES.md`, `ken_share.py` docstring, `assistant/config.py` defaults,
`grep -c "^@app\." server.py`.
