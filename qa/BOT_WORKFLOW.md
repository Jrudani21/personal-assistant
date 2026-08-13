# KEN Bot → Agent Workflow (v1)
# How configured bots feed agents, and agents do deep research.
# Companion to BOT_RULES.md (rules) — this is the operational flow.

```
┌────────────────────────────────────────────────────────────────────┐
│  CONFIG LAYER (deterministic, free)                                │
│  data/bots.json          ← bot registry (schedule, type, topic)     │
│  qa/bot_runner.py        ← "which bots are due?" → work orders      │
│  qa/deep_research.py     ← research bot → self-contained brief      │
│  qa/qa_runner.py         ← QA bot → buglog (no LLM)                 │
│  qa/offpeak_fix.py       ← buglog → open-bug work order             │
└──────────────────────────┬─────────────────────────────────────────┘
                           │ (cron ticks)
                           ▼
┌────────────────────────────────────────────────────────────────────┐
│  AGENT LAYER (LLM, spend justified by value)                       │
│  Agent reads work order → executes:                                │
│    QA bot        → run script (no LLM), or fix crew if new bugs    │
│    Research bot  → orchestrator-worker crew (Anthropic pattern):   │
│                    LEAD decomposes → N parallel subagents (files)  │
│                    → lead synthesizes → cited report → brain note  │
│    Weather bot   → weekly literature sweep → brain/notes           │
│    Fable review  → deep statistical review (manual, pro model)     │
└──────────────────────────┬─────────────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────────────┐
│  OUTPUT LAYER                                                      │
│  data/research/<bot>/report.md  ← cited research reports          │
│  data/qa_buglog.jsonl           ← structured bug history           │
│  E:/Local/brain/                ← Obsidian vault (reports linked)  │
│  Chat delivery                  ← final summary to the user        │
└────────────────────────────────────────────────────────────────────┘
```

## Step 1 — Configure a bot

Edit `data/bots.json`. Fields:

| Field | Meaning |
|---|---|
| `id` | unique slug (e.g. `weather-research`) |
| `type` | `qa` (deterministic) or `research` (LLM crew) |
| `schedule` | `manual`, `hourly`, `daily:HH:MM`, `weekly:day:HH:MM` |
| `topic` | research topic (research bots) |
| `depth` / `subagents` / `model` | crew size + model |
| `sources` | `web_search`, `web_extract`, `arxiv` |
| `output` | report dir |
| `time_sensitive` | `true` = run on schedule even in peak price (live tracking: market scans, price monitors, ledgers). Default false — research/watch defer out of peak. |

## Step 2 — The scheduler tick (cron, deterministic)

`python qa/bot_runner.py` → prints `NO_BOTS_DUE` or work orders.
Runs every 15 min; tracks `last_run` in `data/bots_state.json`.
NO LLM cost.

## Step 3 — The agent executes a work order

- **QA work order**: run the script. If new bugs → the off-peak fix crew
  handles them (03:10 daily). Rules: BOT_RULES.md.
- **RESEARCH work order**: agent runs
  `python qa/deep_research.py <bot_id> --topic "..."` → gets a brief →
  spawns subagents (delegate_task) → synthesizes → saves report →
  links it in the daily brain note.

## Step 4 — Deep research (Anthropic orchestrator-worker)

1. **Lead decomposes** the topic into N non-overlapping angles.
2. **N parallel subagents**, each with: objective + output format
   (markdown file) + tools/sources + task boundary (no duplication).
3. **Filesystem handoff** — subagents write to
   `data/research/<bot>/subagent-N.md` (no game-of-telephone).
4. **Lead synthesizes** into `report.md` with per-claim citations.
5. **Brain link** — the report is referenced in the daily note.

## Step 5 — Token discipline (why this saves money)

- Bots are deterministic until a bot is actually DUE → no LLM on idle ticks.
- Research only fires when a research bot is due (or forced).
- Fix crew only fires when offpeak_fix.py reports OPEN_BUGS.
- Deep research is high-value: justified 15x spend (Anthropic), done once.

## Step 6 — Cron wiring (already live)

| Job | Schedule | What it runs |
|---|---|---|
| KEN QA bot | hourly | qa/qa_runner.py (deterministic) |
| KEN nightly QA | 03:00 daily | sandbox + suite + fix if broken |
| KEN off-peak fix crew | 03:10 daily | offpeak_fix.py → fix crew |
| Bot scheduler | every 15 min | bot_runner.py → due bots |

## Manual triggers

```
python qa/bot_runner.py --list                 # show all bots
python qa/bot_runner.py --force weather-research   # force-run a bot now
python qa/deep_research.py weather-research --topic "..."   # brief
python qa/qa_runner.py --probe-only            # quick health probes
```
