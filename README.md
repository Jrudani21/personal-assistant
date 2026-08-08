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
- `get_datetime` — current date/time (also injected into every system prompt so the model can resolve "tomorrow"/"in 2 hours" without guessing)

## Features

- Multiple chats, persisted as one JSON file each, searchable and exportable to Markdown
- Document upload (txt/md/pdf) for RAG-backed Q&A
- Voice input (transcription) and optional spoken replies
- Graceful degradation if Ollama is unreachable (clear error instead of a crash)
- Long-chat compaction once history nears the context window, old turns summarized, kept out of the displayed/exported chat entirely

## Structure

- `app.py` — Streamlit UI
- `assistant/llm.py` — Ollama chat + tool-calling loop, system prompt assembly
- `assistant/tools.py` — tool implementations + schemas (REGISTRY/SCHEMAS)
- `assistant/memory.py` — JSON key-value store
- `assistant/todo.py` — JSON to-do store
- `assistant/reminders.py` — JSON reminder store
- `assistant/rag.py` — document ingest + hybrid search
- `assistant/sessions.py` — per-chat persistence, search, Markdown export
- `assistant/compaction.py` — long-chat summarization cache
- `assistant/repl.py` / `assistant/repl_worker.py` — persistent Python session for `run_python`
- `assistant/voice.py` — transcription + TTS
