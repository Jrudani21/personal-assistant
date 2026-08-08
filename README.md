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
- `read_file` / `write_file` — sandboxed to `data/workspace/`
- `remember` / `recall` — persistent key-value memory in `data/memory.json`
- `get_datetime` — current date/time

## Structure

- `app.py` — Streamlit chat UI
- `assistant/llm.py` — Ollama chat + tool-calling loop
- `assistant/tools.py` — tool implementations + schemas
- `assistant/memory.py` — JSON key-value store
