"""Central JSON-backed configuration for the assistant.

All user-configurable settings (tools, rules, setup) live in one place:
``data/config.json`` (gitignored — a local machine preference file, not
source). Modules read through :func:`get`; the Streamlit settings page
(``pages/1_⚙️_Settings.py``) writes through :func:`set`. If a key isn't in
config.json, :func:`get` falls back to the module's hardcoded default, so
nothing breaks when the file is absent.

Precedence for path-like settings (workspace, vault, backup dir):
    config.json  ->  env var  ->  module default

Rules of the road (mirrors the deepseek-cave CREW_RULES):
- One store, no hardcoded drift: if a knob is configurable, its default
  lives here (or in the owning module) and config only overrides it.
- Test before you trust: `python -m pytest tests/ -q` after changing this.
- Changes take effect on the next read; most settings apply immediately,
  some (paths) only to new calls/sessions.
"""
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "data" / "config.json"

# Defaults mirror the values hardcoded in the modules before config existed.
# "" means "auto" / "use the module default" for path & model settings.
DEFAULTS = {
    # ------------------------------------------------------------ setup
    "default_model": "",            # "" = auto-pick first model Ollama reports
    "ollama_base_url": "http://127.0.0.1:1234",   # LM Studio (Ollama uninstalled 2026-09-01)
    "workspace_dir": "",            # "" = <project>/data/workspace
    "vault_dir": "",                # "" = env OBSIDIAN_VAULT, else ~/brain
    "knowledge_dir": "",            # "" = env KNOWLEDGE_DIR, else deepseek-cave/nightly-learn/knowledge
    "backup_dir": "",               # "" = env BACKUP_DIR, else <project>/backups
    "backup_retention": 10,         # newest N backups kept
    # ------------------------------------------------------------ tools
    "enabled_tools": [],            # [] = all tools enabled; else list of names
    "web_search_max_results": 5,
    "run_python_timeout_s": 15,
    "read_file_max_chars": 8000,
    # ------------------------------------------------------------ rules
    "system_prompt": (
        "You are Janak's personal AI assistant, running fully locally via Ollama "
        "(no API cost). Be direct and concise. Use tools when you need current "
        "info, math, or file access. Use 'remember' whenever the user shares a "
        "durable fact/preference about themselves. "
        "Never invent tool results — call the tool. "
        "When you answer using search_documents results, cite the numbered "
        "sources inline like [1], [2] matching the tool output's numbering."
    ),
    "max_tool_rounds": 6,
    "max_memory_facts": 30,         # facts injected into the system prompt
    "context_gist": "",             # brain+memory context pack injected into the system prompt (set via the Brain & Memory page)
    "max_gist_chars": 4000,         # cap on the injected context pack (prompt crowding)
    "rag_embed_model": "nomic-embed-text",
    "rag_chunk_size": 800,
    "rag_chunk_overlap": 150,
    "rag_min_similarity": 0.5,
    "rag_rrf_k": 60,
    "rag_search_top_k": 4,
    "crew_fast_model": "ollama/qwen2.5:7b",
    "crew_fallback_model": "openai/qwen/qwen3-8b",
    "crew_timeout_s": 180,          # DeepSeek analysis step timeout
    # ------------------------------------------------------------ voice
    "whisper_model": "base",        # faster-whisper size: tiny/base/small/medium/large-v3
    "speak_replies": False,         # default for the "🔊 Speak replies" toggle
    # ------------------------------------------------ researched tools
    "mcp_servers": [],              # [{"name", "command", "args"}] — MCP stdio servers
    "mcp_timeout_s": 30,
    "sqlite_db": "",               # "" = data/assistant.db (query_sql target)
    "skills_dir": "",              # "" = data/skills
    "skill_timeout_s": 60,
    "fetch_webpage_max_chars": 8000,
}

_cached = None


def _load() -> dict:
    global _cached
    if _cached is None:
        try:
            _cached = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cached = {}
    return _cached


def _save() -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(_cached, indent=2), encoding="utf-8")


def get(key: str, default=None):
    """Read a setting: config.json override, else the caller's default,
    else the built-in default table."""
    cfg = _load()
    if key in cfg:
        return cfg[key]
    if default is not None:
        return default
    return DEFAULTS.get(key)


def set(key: str, value) -> None:
    """Write a setting and persist it to data/config.json."""
    cfg = _load()
    cfg[key] = value
    _save()


def all() -> dict:
    """Merged view: every key with its effective value (defaults + overrides)."""
    merged = dict(DEFAULTS)
    merged.update(_load())
    return merged


def reset() -> str:
    """Delete data/config.json — everything returns to built-in defaults."""
    global _cached
    _cached = {}
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
        return "Config reset to defaults."
    return "Config was already at defaults."


def tool_enabled(name: str) -> bool:
    """True when a tool should be offered to the model. [] means all on."""
    enabled = _load().get("enabled_tools")
    if not enabled:
        return True
    return name in enabled
