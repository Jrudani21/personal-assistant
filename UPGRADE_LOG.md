# Upgrade log — 2026-08-08

## Session 5 — local snapshot backups

Per user request ("also keep saving locally"): everything the assistant
knows is now snapshotted locally, independent of GitHub.

- **Local backups** (`assistant/backup.py`): the whole `data/` folder
  (memory, chats, observations, embeddings, tasks, reminders, workspace) is
  copied into timestamped `backups/<ts>/` folders. Retention keeps the
  newest 10; writes go to a temp dir then atomic rename so a crash can't
  leave a half-written snapshot. Restore support included.
- **Auto-backup on app start**: `backup_if_due()` creates one backup per
  day (no-op if today's exists), wired into app startup — a rolling daily
  local history with zero effort.
- **UI + tool**: "💾 Back up data now" sidebar button (shows last backup
  time / count / size) and a `backup_data` tool the model can call.
- `backups/` is gitignored (local-only, like the rest of data/).

Verified: `py_compile` clean; 152 tests passing (was 145); real backup
created and listed; Streamlit boots on :8599 (HTTP 200).

## Session 4 — append-only JSONL chats, memory distillation, git-backed memory

Implemented the next ranked upgrades from the nanobot/claude-mem study:

- **Append-only JSONL chats** (`assistant/sessions.py`): chats are now
  `<id>.jsonl` (one JSON line per message, O(1) crash-safe appends — no more
  per-turn full-file rewrite) + `<id>.meta.json` (tiny sidecar for title /
  compaction cache / message_count, rewritten only on rare meta changes).
  Legacy `<id>.json` chats still load and migrate on first save. `load`,
  `list_chats`, `search_chats`, `delete`, `export_markdown` all handle both
  formats. Verified: incremental appends, compaction persistence across
  saves, legacy migration, healing when a caller holds a truncated view.
- **Memory distillation** (`assistant/distill.py`): the "Dream-lite"
  self-training feature. `distill()` reads the recent observation log,
  asks the local model for strict-JSON durable facts
  (key/value/facts/concepts), and adds only NEW keys to memory — existing
  memory is never overwritten. Guardrails: defensive JSON parsing (markdown
  fences, prose, garbage all rejected), max 5 new keys/run, sane key regex,
  graceful degradation on Ollama failure. New `distill_memory` tool + "🧠
  Learn from activity" sidebar button. Smoke-tested end-to-end against the
  real qwen2.5:7b model: extracted 3 structured facts from seeded activity.
- **Git-backed memory**: `.gitignore` now tracks `data/memory.json` (only
  that file — chats/embeddings/observations stay out of git) so memory
  changes are versioned and revertible in the private repo.

Verified: `py_compile` clean; 145 tests passing (was 130); Streamlit boots
on :8599 (HTTP 200).

## Session 3 — structured memory + observation capture

From the repo study of nanobot/claude-mem (see
brain/notes/Memory system design study.md), implemented the first two ranked
upgrades:

- **Structured memory** (`assistant/memory.py`): entries are now
  `{value, facts[], concepts[], updated_at}` instead of flat `key: value`.
  `facts` are discrete verifiable claims, `concepts` are tags for retrieval/
  dedup (claude-mem's memory-item idea). Fully backward compatible — legacy
  plain strings and value-only entries still read correctly. The `remember`
  tool schema accepts optional `facts`/`concepts` arrays; the system prompt
  renders them inline; the 30-entry prompt cap still applies.
- **Observation capture** (`assistant/observations.py`): every tool call is
  appended to `data/observations.jsonl` (timestamp + truncated args/result +
  truncation flags), wired into both `run_chat` and `stream_chat` in
  `llm.py`. Append-only JSONL (nanobot's pattern): O(1) crash-safe writes,
  size-capped (~2 MB) with compaction keeping the newest 2000 entries.
  Logging failures are swallowed so capture never breaks a chat. New
  `recent_activity` tool lets the model recall what it has actually done.
- `data/memory.json` enriched: all 10 entries now carry facts + concepts.

Verified: `py_compile` clean; 130 tests passing (was 115); smoke-tested
memory rendering in the system prompt and observation append/read.

## Session 2 — deep-analysis caching, numeric-consistency guardrail, reminder daemon

## Session 2 — deep-analysis caching, numeric-consistency guardrail, reminder daemon

Shipped (all tested, 115 tests passing):

- **Deep-analysis caching** (`assistant/crew_cache.py`): re-running the same
  topic now returns the stored report instantly instead of paying the full
  ~45-120s four-agent cost. Keyed by whitespace/case-insensitive hash of the
  input, 7-day TTL, capped at 50 entries (oldest evicted). Two deliberate
  rules: local-fallback results are NEVER cached (they can misstate numeric
  comparisons — see the brain note), and failures are never cached. New
  `clear_crew_cache` tool + sidebar button. Closes the project note's
  "No caching" open question.
- **Numeric-consistency guardrail** (`assistant/crew.py`): closes the open
  item from the "Local models invert numeric comparisons under prompt
  crowding" note. The Quant stage's accepted output is captured in shared
  state; the Analyst and Reporter guardrails then deterministically parse
  "X is higher/lower than Y" claims and cross-check each against the
  verified figures. Contradictions reject the output with a correction
  message naming the true figures, forcing a CrewAI retry. Entity matching
  is fuzzy ("Product C" matches a figure stored as "Product C revenue per
  unit") and ambiguous matches are skipped rather than misjudged.
- **Background reminder daemon** (`assistant/reminder_daemon.py`): reminders
  now fire even when the app is closed. `--install` registers a logon
  scheduled task (hidden, via pythonw); `--once` fires due reminders and
  exits; toasts fall back to a classic `msg` popup; reminders are marked
  fired after a delivery attempt so they can never double-fire. The app
  keeps its own in-app check as before.
- **Smarter chat titles** (`assistant/sessions.py`): titles now strip
  conversational fillers ("can you", "hey", ...), keep the first sentence,
  and truncate at a word boundary instead of raw 40-char slices.
- **Memory hygiene** (`assistant/memory.py`, `assistant/llm.py`): memory
  values are now timestamped, and the system prompt injects only the 30 most
  recent facts (with a note that more exist) instead of everything —
  bounding prompt growth that would crowd a small local model. Legacy
  plain-string entries are still read.

Verified: `py_compile` clean on all 28 files; 115 tests pass; `--once`
smoke-tested (no reminders on this machine).

## Session 1 — UI polish, tools, compaction, persistent REPL

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

## Shipped — round 4: persistent Python REPL

Implemented `assistant/repl.py` + `assistant/repl_worker.py`, replacing
`run_python`'s one-shot `subprocess.run` (no state) with one long-lived
worker subprocess for the whole app — variables/imports/dataframes now
survive across separate `run_python` calls until explicitly restarted.
Scoped as one global session (not per-chat) — simplest useful version for a
single local user; a "Restart Python session" button + `restart_python_session`
tool reset it on demand.

Two real design risks here, both tested rather than assumed:
- **Windows has no `select()` on pipes** — reading the worker's response
  with a timeout needed a background reader thread pushing lines into a
  `queue.Queue`, not a blocking read. Verified: state persists across calls,
  stdout/stderr captured separately from the JSON control channel (user
  `print()` can't corrupt the protocol since it's redirected to a buffer
  inside the worker before the response line is written), exceptions
  surface with a full traceback while leaving prior variables intact,
  timeout (tested with a 1.5s override on a 5s sleep) auto-restarts and
  reports correctly, `restart()` cleanly terminates the process
  (confirmed via `.poll()`).
- **Orphan-process risk if the parent gets force-killed** (a real concern —
  I've been using `Stop-Process -Force` to restart Streamlit all session).
  Tested directly: closed the worker's stdin the way a force-killed parent's
  OS-level handle cleanup would, without calling `restart()` — worker exited
  on its own (exit code 0) via EOF on its `for line in sys.stdin` read loop.
  No orphan. Confirmed via `Get-Process python*` after testing: only the
  Streamlit server process itself, no leftover workers.
- Full path tested through the real LLM tool-calling loop: asked it to set
  a variable in one `run_python` call and use it unmodified in a second —
  correct (`total = 15` → `total * 2 = 30` in the next call).
- Found and fixed one bug along the way: the timeout error message
  hardcoded "15s limit" regardless of the actual timeout passed in —
  harmless today (only the default is used) but wrong if ever called
  otherwise; now interpolates the real value.

`python -m py_compile` clean; Streamlit restarted clean on :8501.

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
