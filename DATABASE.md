# Data stores - KEN personal assistant

Everything lives under `data/` (path constants in each module). `.gitignore` excludes
`data/*`, including `data/memory.json` (personal profile facts, kept local only). Backups of the whole folder go to `backups/` (gitignored) or `BACKUP_DIR`.

## SQLite

| File | Owner | Tables |
|---|---|---|
| `data/work_queue.sqlite` | `qa/work_queue.py` | `queue(id PK, key UNIQUE (idempotency: bot id + run ts), bot_id, work_order, state pending/claimed/done/dead, attempts, next_retry, created, claimed_at, completed_at, dead_reason)`; index `(state, next_retry)` |
| `data/event_bus.sqlite` | `qa/event_bus.py` | `events(id PK, type, payload, ts)`; index `(type, ts)` |
| `data/fleet_state.db` | `qa/fleet_common.py` | `checkpoints(bot PK, step, data, ts)`, `idempotency(key PK, ts)` |
| `data/assistant.db` (or config `sqlite_db`) | user data for the `query_sql` tool | schema is whatever the user puts there; the tool only allows `SELECT`/`PRAGMA`/`EXPLAIN`/`WITH` and caps results at 50 rows |

## JSON / JSONL

| File | Module | Shape |
|---|---|---|
| `data/memory.json` (local only, gitignored) | `assistant/memory.py` | object of 17 entries `key -> {value, facts[], concepts[], updated_at}`; prompt injects the 30 most recent |
| `data/chats/<id>.jsonl` + `<id>.meta.json` | `assistant/sessions.py` | append-only messages + title/compaction sidecar; legacy `<id>.json` migrated on save |
| `data/observations.jsonl` | `assistant/observations.py` | one line per tool call (size-capped) |
| `data/embeddings.json` | `assistant/rag.py` | uploaded-document chunks + vectors |
| `data/config.json` | `assistant/config.py`, `server.py` | settings overriding `DEFAULTS` |
| `data/todo.json`, `data/reminders.json` | `todo.py`, `reminders.py` | task and reminder lists |
| `data/bots.json`, `data/bots_state.json`, `data/approvals.jsonl` | `qa/bot_runner.py`, `fleet_boss.py`, `server.py` | bot fleet config, run state, approval log (also read by the fleet dashboards) |
| `data/.ken_tokens.json` (legacy `data/.ken_token`) | `server.py` | owner + guest access tokens |
| `data/.ken_users.json` | `server.py` | user accounts, passwords as salted scrypt hashes |
| `data/access.log` | `server.py` | auth log |
| `data/skills/<name>/` | `assistant/skills.py` | `SKILL.md` + optional `run.py` |
| `data/workspace/` | `assistant/tools.py` | sandbox for file tools and `run_python` |

Other files seen in `data/` (logs, QA state, provider health, screenshots, `game_mode*.json`)
are runtime artefacts of `qa/` scripts.

## Retention

`assistant/backup.py`: snapshot of `data/` once per day on app start or on demand, newest 10
kept. No retention/deletion policy for chats, observations or logs. Unknown - needs owner input.

## Verified against

`CREATE TABLE` statements in `qa/work_queue.py`, `qa/event_bus.py`, `qa/fleet_common.py`;
path constants in `assistant/*.py`, `qa/*.py`, `server.py` (lines 75, 269-278, 402-407);
`assistant/tools.py::query_sql`; `.gitignore`; key names of `data/memory.json`
(values not reproduced); `README.md` features list; `ls data`.
