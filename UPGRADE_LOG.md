# Upgrade log — 2026-08-08

Session scope: UI polish, tool-registry additions from public-repo research, global
Claude Code subagent audit. Everything below either shipped, is waiting on your
review, or was blocked and skipped rather than guessed at.

## Shipped (implemented + smoke-tested this session)

- **UI polish** (`app.py`): custom CSS (chat bubble shadows, sidebar button
  alignment, tighter dividers), empty-state welcome message, tool-call log moved
  from an always-open info box to a collapsed expander.
- **RAG citations** (`assistant/rag.py`, `assistant/llm.py`): `search_documents`
  now returns numbered `[1] [2]` sources instead of a raw filename dump; system
  prompt tells the model to cite them inline. Source: open-webui's citation UX.
- **Todo scratchpad** (`assistant/todo.py`): `add_task` / `list_tasks` /
  `complete_task` / `clear_tasks`, same JSON-store pattern as `memory.py`.
  Source: AnythingLLM's single-purpose skill pattern.
- **Reminders** (`assistant/reminders.py`): `remind_me` / `list_reminders` /
  `cancel_reminder`. No background daemon — checked on each Streamlit rerun, so
  it only fires while the app is open. Due reminders toast once (session-local
  dedup) and get folded into the system prompt once (persisted `fired` flag)
  so the model mentions them without repeating on every turn. Source: Khoj's
  automations, scoped down to zero-infra.

All five new tools are registered in `assistant/tools.py` REGISTRY + SCHEMAS.
`python -m py_compile` clean on every touched file; todo/reminders modules
smoke-tested directly (add/list/complete/clear, due-detection, fire-once,
cancel — all correct); Streamlit restarted clean on :8501 with no import
errors.

**Round 2** (after `gh` setup, see below):

- **Bug fix — reminder date grounding**: caught via live test — asking for
  "tomorrow at 9am" resolved against the model's training-cutoff date
  (2023) instead of the real date, so a reminder would've been born already
  overdue. `assistant/llm.py`'s system prompt now injects the actual current
  date/time every turn; retested, resolves correctly (`2026-08-09T09:00`).
- **Sidebar visibility for tasks/reminders** (`app.py`, `assistant/todo.py`,
  `assistant/reminders.py`): tasks and reminders were chat-tool-only, no UI
  surface, unlike memory which already had a sidebar section. Added
  checkbox/delete UI mirroring the existing memory pattern; added
  `todo.get_tasks`/`set_task_done`/`delete_task` and
  `reminders.get_reminders` to support it. Smoke-tested directly (toggle
  done/undone, delete — correct).
- **Output truncation with explicit notice** (`assistant/tools.py`) — from
  reading prime-agent's actual `truncate.ts` source (see below): `read_file`
  and `run_python` already capped output by char count but did it silently,
  which can mislead the model into thinking it saw the whole thing. Added
  `_truncate_head`/`_truncate_tail` helpers that append/prepend a
  `[...N truncated]` notice. Tested with a 9000-char file and 5000-char
  script output — both truncate correctly with a visible notice.
- **Ollama context-overflow detection** (`assistant/llm.py`) — generic
  `except Exception` previously made a real context-overflow error
  indistinguishable from a dead connection. Added a regex match on Ollama's
  documented overflow string (`prompt too long; exceeded ... context
  length`), returns a clear "start a new chat" message instead. Unit-tested
  both branches (overflow string vs. generic error) — correct.
- **Tighter tool-schema descriptions** (`assistant/tools.py`) — `remember`,
  `complete_task`, `cancel_reminder` descriptions now state the actual
  failure mode (key-overwrite behavior, IDs not reused after
  delete/cancel) instead of a generic label — a zero-cost tool-call
  reliability improvement, per prime-agent's `edit.ts` parameter-description
  pattern.

`python -m py_compile` clean; Streamlit restarted clean on :8501 after each
change.

## Needs your review before I touch anything (per your "log, don't apply" call)

**Global Claude Code subagents** — researched, NOT installed, since this
affects every project you use Claude Code on, not just this one:

- **SQL specialist** (`sql-pro` / `database-optimizer`) — from
  [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents).
  Real gap: nothing in your current set is tuned for query optimization or
  schema design, and SQL is in your stated stack. Caveat: single-maintainer
  collection tracking `main`, no versioned releases — if you want this, I'd
  copy just that one agent `.md` into `~/.claude/agents/`, not add the whole
  marketplace.
- **Test-writing specialist** (`test-automator` from the same repo, or
  `tdd-workflows` from [wshobson/agents](https://github.com/wshobson/agents))
  — lower priority, your scripts are mostly analysis not production code
  needing regression suites.
- Everything else evaluated (doc-generation, perf-profiling, dependency
  audit) was judged low-value or already covered by what you have —
  no case made for touching the caveman toolkit or built-ins. Full
  conservative-audit reasoning available if you want it.

## Deferred — bigger effort, not attempted this session (avoiding a rushed
half-implementation of something you haven't seen the design for)

- **Persistent Python REPL session** (medium-large effort) — `run_python`
  currently spins a fresh subprocess per call with no state; a long-lived
  kernel would let multi-step pandas/stats workflows keep variables alive
  across calls. Real complexity jump from the current sandboxed one-shot
  subprocess — worth it only if that workflow comes up often. Source:
  open-webui's code-interpreter.
- **Append-only JSONL session writes** (low value, low urgency) —
  `sessions.py.save()` rewrites the entire chat JSON file on every turn;
  prime-agent's `session-manager.ts` appends one JSONL line per turn and
  only rewrites on rare full edits (compaction). Real but minor win
  (crash-safety, O(1) vs O(n) writes) — only worth it if chat files get
  large. Their own research note called this "not urgent."

## Shipped — round 3: conversation compaction

Implemented `assistant/compaction.py`. Resolved the round-2 tradeoff without
picking a side: the cache lives in `chat['compaction']`, a field outside
`chat['messages']` entirely — the chat UI, export, and search all iterate
`messages` only, so they never see anything different. Only what's sent to
Ollama changes.

- Trigger: history estimated over 4000 tokens (chars/4 heuristic), leaving
  headroom in the real `OLLAMA_CONTEXT_LENGTH=8192` on this machine
  (confirmed via `ollama show`/`ollama ps`, not guessed) for system prompt +
  tool schemas + response.
- Cut point always lands on a `user`-role message, snapped to buckets of 10
  so the cache survives many turns instead of re-summarizing on every
  message — verified with a synthetic 62-message history that cuts never
  land mid tool-call round (would otherwise send Ollama an orphaned tool
  result and error).
- One extra LLM call to summarize the old portion into a fixed template
  (Goal/Constraints/Progress/Key decisions/Next steps); cached and reused
  until the cut point shifts.
- Degrades to raw history if summarization fails (Ollama down) — verified
  via the `OLLAMA_HOST` outage-simulation trick, no crash, no bad cache
  written.
- Wired into `app.py`'s single `stream_chat` call site via
  `compaction.get_llm_messages(chat, model)`.

Tested: unit tests on the safe-cut logic (multiple targets, all land on
`user`), short-chat passthrough (returns the same object, no-op), full
trigger with a real 80-message synthetic chat via the actual model
(compacted to 21 messages, cache verified stable across repeat calls,
original `chat['messages']` left at full 80), outage fallback, and a full
path test through the real `stream_chat` — asked about the most recent of
40 numbered facts, correctly answered from the verbatim tail. All test
chats cleaned up after (`sessions.delete`), no leftover data. `py_compile`
clean, Streamlit restarted clean on :8501.

## Resolved this round

- **GitHub CLI (`gh`)** — installed via `winget install GitHub.cli`,
  authenticated as `Jrudani21` via device-code flow (you approved in
  browser), added to PATH via `.bashrc` so future sessions pick it up
  automatically. `gh auth status` confirms active.
- **prime-agent research repo** — forked `PrimeIntellect-ai/prime-agent` to
  `Jrudani21/prime-agent`, cloned to
  `C:\Users\Janak's PC\projects\research\prime-agent` (sibling folder,
  outside personal-assistant's own git repo — no nesting), checked out onto
  a local `research/study` branch so `main` stays untouched. Mined its
  actual source (not just docs) for the compaction/truncation/error-handling
  findings above.

## Not yet committed

Nothing in this session has been `git commit`-ed — say the word and I'll
stage + commit (or split into separate commits per feature if you'd rather
review them individually).
