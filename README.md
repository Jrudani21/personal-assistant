# Personal Assistant

Local, free personal AI assistant. Runs on Ollama — no API cost.

## Run

```
pip install -r requirements.txt
streamlit run app.py
```

Needs Ollama running (`ollama serve`) with at least one model pulled
(e.g. `ollama pull qwen2.5:7b`).

## Tools

- `calculator` — arithmetic
- `web_search` — DuckDuckGo, no API key
- `weather` — current conditions via Open-Meteo, no API key
- `wikipedia_summary` — short topic summary
- `read_file` / `write_file` / `list_files` — sandboxed to `data/workspace/`
- `run_python` / `restart_python_session` — persistent session (variables survive across calls), 15s timeout per call, cwd locked to workspace
- `search_documents` — hybrid BM25+cosine RAG over uploaded docs, numbered `[1] [2]` citations
- `remember` / `recall` / `forget` — persistent key-value memory in `data/memory.json`
- `add_task` / `list_tasks` / `complete_task` / `clear_tasks` — local to-do scratchpad
- `remind_me` / `list_reminders` / `cancel_reminder` — reminders, checked on each app rerun (only fire while the app is open, no background daemon)
- `deep_analysis` — 4-agent CrewAI pipeline (fetch → verify/compute → analyze → report) for multi-step questions; the analysis step calls Claude Code CLI, with a local-model fallback. Results are cached for 7 days (identical re-runs return instantly; local-fallback results are never cached).
- `clear_crew_cache` — forget cached deep-analysis reports so the next identical request re-runs the full pipeline
- `get_datetime` — current date/time (also injected into every system prompt so the model can resolve "tomorrow"/"in 2 hours" without guessing)

## Features

- Multiple chats, persisted as one JSON file each, searchable and exportable to Markdown
- Document upload (txt/md/pdf) for RAG-backed Q&A
- Voice input (transcription) and optional spoken replies
- Graceful degradation if Ollama is unreachable (clear error instead of a crash)
- Long-chat compaction once history nears the context window, old turns summarized, kept out of the displayed/exported chat entirely
- Smarter auto-titles (filler stripped, first sentence, word-boundary truncation)
- Deep-analysis caching: identical topics within 7 days return the stored report instantly (local-fallback results deliberately never cached)

## Reminder daemon (optional)

Reminders normally only fire while the app is open. For 24/7 delivery, run a
small background daemon that pops a Windows toast when a reminder comes due:

```
python assistant/reminder_daemon.py --install   # registers a logon scheduled task (runs hidden)
python assistant/reminder_daemon.py --once      # fire due reminders now, then exit
python assistant/reminder_daemon.py --uninstall # remove the scheduled task
```

Toasts are best-effort (falls back to a classic `msg` popup), and a reminder
is marked fired after a delivery attempt so it can never double-fire.

## Structure

- `app.py` — Streamlit UI
- `assistant/llm.py` — Ollama chat + tool-calling loop, system prompt assembly
- `assistant/tools.py` — tool implementations + schemas (REGISTRY/SCHEMAS)
- `assistant/memory.py` — JSON key-value store (timestamped; system prompt injects only the 30 most recent facts)
- `assistant/todo.py` — JSON to-do store
- `assistant/reminders.py` — JSON reminder store
- `assistant/reminder_daemon.py` — background toast daemon (optional)
- `assistant/rag.py` — document ingest + hybrid search
- `assistant/sessions.py` — per-chat persistence, search, Markdown export
- `assistant/compaction.py` — long-chat summarization cache
- `assistant/repl.py` / `assistant/repl_worker.py` — persistent Python session for `run_python`
- `assistant/crew.py` — CrewAI deep-analysis pipeline + numeric-consistency guardrail
- `assistant/crew_cache.py` — deep-analysis result cache
- `assistant/voice.py` — transcription + TTS
