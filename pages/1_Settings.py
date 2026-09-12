"""⚙️ Settings — configure tools, rules, and setup for the personal assistant.

Everything here writes to data/config.json via assistant/config.py. Most
settings apply immediately (they're read on every tool call / chat turn);
a few (paths, models) affect the next call or require restarting the app.

Run from the project root alongside app.py:
    streamlit run app.py      →  "⚙️ Settings" appears in the sidebar
"""
import json
import subprocess

import streamlit as st

from assistant import backup, config, crew_cache, memory, repl
from assistant.reminder_daemon import TASK_NAME as DAEMON_TASK
from assistant.tools import REGISTRY, SCHEMAS

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="centered")

st.title("⚙️ Settings")
st.caption(
    "Configure the assistant's tools, behavior rules, and machine setup. "
    "Saved to `data/config.json` — most changes apply immediately, "
    "path/model changes apply to the next call."
)

# Tool name → description, built once from the schemas.
_DESC = {
    s["function"]["name"]: s["function"].get("description", "")
    for s in SCHEMAS
}


def _save(key, value):
    config.set(key, value)
    st.toast(f"Saved {key}")


def _daemon_installed() -> bool:
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", DAEMON_TASK],
            capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0
    except Exception:
        return False


def _daemon_toggle():
    if _daemon_installed():
        st.success("✅ Reminder daemon installed — toasts fire 24/7 at logon.")
        if st.button("🗑 Uninstall daemon", use_container_width=True):
            st.toast(_daemon_uninstall())
            st.rerun()
    else:
        st.info("Reminders only fire while the app is open. Install the daemon for 24/7 toasts.")
        if st.button("📟 Install daemon (logon)", use_container_width=True):
            st.toast(_daemon_install())
            st.rerun()


def _daemon_install() -> str:
    try:
        from assistant.reminder_daemon import install
        return install()
    except Exception as e:
        return f"Install failed: {e}"


def _daemon_uninstall() -> str:
    try:
        from assistant.reminder_daemon import uninstall
        return uninstall()
    except Exception as e:
        return f"Uninstall failed: {e}"


# Local model server reachability (best-effort — settings must work without it too).
try:
    from assistant import local_llm as _local_llm
    _models = [m["model"] for m in _local_llm.list_models().get("models", [])]
    _local_ok = True
    _local_desc = _local_llm.describe()
except Exception:
    _models = []
    _local_ok = False
    _local_desc = "no local model server"

tab_tools, tab_rules, tab_setup, tab_data = st.tabs(
    ["🧰 Tools", "📜 Rules & Behavior", "⚙️ Setup", "💾 Data"]
)

# ---------------------------------------------------------------------------
# 🧰 TOOLS
# ---------------------------------------------------------------------------
with tab_tools:
    st.subheader("Enabled tools")
    st.caption(
        "Tools are offered to the model as function calls. Disabling one hides it "
        "from the model entirely — quick math, files, web, memory, tasks, etc."
    )

    all_names = sorted(REGISTRY.keys())
    # Sentinel for "no tools enabled". The allowlist schema uses [] = all-on
    # (keeps config small), so "all disabled" needs a value that is truthy but
    # matches no real tool name — tool_enabled() then returns False for every
    # tool with zero changes to config.py / tools.py.
    NONE_SENTINEL = "__none__"
    current = config.get("enabled_tools") or all_names  # [] = everything

    cols = st.columns([1, 3])
    if cols[0].button("✅ Enable all", use_container_width=True):
        _save("enabled_tools", [])
        st.rerun()
    if cols[1].button("🚫 Disable all", use_container_width=True):
        _save("enabled_tools", [NONE_SENTINEL])
        st.rerun()

    changes = {}
    with st.container(border=True):
        for name in all_names:
            enabled = name in current
            on = st.checkbox(name, value=enabled, key=f"tool_{name}")
            if on != enabled:
                changes[name] = on
            desc = _DESC.get(name, "")
            if desc:
                st.caption(desc[:160])
    if changes:
        enabled = set(current) if current != all_names else set(all_names)
        for name, on in changes.items():
            if on:
                enabled.add(name)
            else:
                enabled.discard(name)
        # [] means "all enabled" — keep the file small and the meaning clear.
        _save("enabled_tools", sorted(enabled) if enabled != set(all_names) else [])
        st.rerun()

    st.divider()
    st.subheader("Per-tool parameters")
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            val = st.number_input(
                "Web search: max results",
                min_value=1, max_value=20, step=1,
                value=int(config.get("web_search_max_results", 5)),
                key="ws_max",
            )
            if st.button("Save", key="save_ws", use_container_width=True):
                _save("web_search_max_results", int(val))
        with c2:
            val = st.number_input(
                "Python: timeout (s)",
                min_value=1, max_value=120, step=1,
                value=int(config.get("run_python_timeout_s", 15)),
                key="py_to",
            )
            if st.button("Save", key="save_py", use_container_width=True):
                _save("run_python_timeout_s", int(val))
        with c3:
            val = st.number_input(
                "Read file: max chars",
                min_value=500, max_value=100000, step=500,
                value=int(config.get("read_file_max_chars", 8000)),
                key="rf_mc",
            )
            if st.button("Save", key="save_rf", use_container_width=True):
                _save("read_file_max_chars", int(val))

# ---------------------------------------------------------------------------
# 📜 RULES & BEHAVIOR
# ---------------------------------------------------------------------------
with tab_rules:
    st.subheader("System prompt")
    st.caption(
        "The base instructions injected into every chat. Date/time and remembered "
        "facts are appended automatically after this text."
    )
    prompt = st.text_area(
        "System prompt",
        value=config.get("system_prompt", ""),
        height=220, key="sys_prompt",
    )
    if st.button("💾 Save system prompt", use_container_width=True):
        if prompt.strip():
            _save("system_prompt", prompt.strip())
        else:
            st.warning("System prompt can't be empty.")
    if st.button("↩️ Restore default prompt", use_container_width=True):
        config.set("system_prompt", config.DEFAULTS["system_prompt"])
        st.rerun()

    st.divider()
    st.subheader("Conversation")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            rounds = st.number_input(
                "Max tool rounds per reply",
                min_value=1, max_value=20, step=1,
                value=int(config.get("max_tool_rounds", 6)), key="mtr",
            )
            if st.button("Save", key="save_mtr", use_container_width=True):
                _save("max_tool_rounds", int(rounds))
        with c2:
            facts = st.number_input(
                "Memory facts injected into prompt",
                min_value=1, max_value=100, step=1,
                value=int(config.get("max_memory_facts", 30)), key="mmf",
            )
            if st.button("Save", key="save_mmf", use_container_width=True):
                _save("max_memory_facts", int(facts))

    st.divider()
    st.subheader("Documents & RAG")
    st.caption("Search over uploaded docs + Obsidian vault. Changes apply to the next ingest/search.")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            emb = st.text_input("Embedding model", value=config.get("rag_embed_model", "nomic-embed-text"), key="emb")
            chunk = st.number_input("Chunk size (chars)", min_value=100, max_value=5000, step=50,
                                    value=int(config.get("rag_chunk_size", 800)), key="chunk")
            overlap = st.number_input("Chunk overlap (chars)", min_value=0, max_value=1000, step=25,
                                      value=int(config.get("rag_chunk_overlap", 150)), key="overlap")
        with c2:
            sim = st.slider("Min similarity", 0.0, 1.0,
                            value=float(config.get("rag_min_similarity", 0.5)), step=0.05, key="sim")
            rrf = st.number_input("RRF constant (k)", min_value=1, max_value=200, step=1,
                                  value=int(config.get("rag_rrf_k", 60)), key="rrf")
            topk = st.number_input("Search top-k", min_value=1, max_value=20, step=1,
                                   value=int(config.get("rag_search_top_k", 4)), key="topk")
        if st.button("💾 Save RAG settings", use_container_width=True):
            _save("rag_embed_model", emb.strip())
            _save("rag_chunk_size", int(chunk))
            _save("rag_chunk_overlap", int(overlap))
            _save("rag_min_similarity", float(sim))
            _save("rag_rrf_k", int(rrf))
            _save("rag_search_top_k", int(topk))

    st.divider()
    st.subheader("Deep analysis crew")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            fast = st.text_input("Fast model (fetch/quant/report)", value=config.get("crew_fast_model", ""), key="cfm")
            cto = st.number_input("Claude step timeout (s)", min_value=30, max_value=600, step=10,
                                  value=int(config.get("crew_timeout_s", 180)), key="cto")
        with c2:
            fb = st.text_input("Fallback reasoning model", value=config.get("crew_fallback_model", ""), key="cbm")
        if st.button("💾 Save crew settings", use_container_width=True):
            if fast.strip():
                _save("crew_fast_model", fast.strip())
            if fb.strip():
                _save("crew_fallback_model", fb.strip())
            _save("crew_timeout_s", int(cto))

    st.divider()
    st.subheader("Voice")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            wm = st.selectbox(
                "Whisper model (STT quality/speed)",
                ["tiny", "base", "small", "medium", "large-v3"],
                index=["tiny", "base", "small", "medium", "large-v3"].index(
                    config.get("whisper_model", "base")
                ) if config.get("whisper_model", "base") in ["tiny", "base", "small", "medium", "large-v3"] else 1,
                key="wm",
            )
            if st.button("Save", key="save_wm", use_container_width=True):
                _save("whisper_model", wm)
        with c2:
            sr = st.checkbox("Speak replies by default", value=bool(config.get("speak_replies", False)), key="sr")
            if st.button("Save", key="save_sr", use_container_width=True):
                _save("speak_replies", bool(sr))

# ---------------------------------------------------------------------------
# ⚙️ SETUP
# ---------------------------------------------------------------------------
with tab_setup:
    st.subheader("Model & Ollama")
    if _local_ok:
        st.success(f"✅ Local server reachable — {_local_desc} — {len(_models)} model(s) available.")
        idx = _models.index(config.get("default_model")) if config.get("default_model") in _models else 0
        model = st.selectbox("Default chat model", _models, index=idx, key="def_model")
        if st.button("Save", key="save_def_model", use_container_width=True):
            _save("default_model", model)
    else:
        st.warning("⚠️ Ollama not reachable. Start it (`ollama serve`) and reload.")
        model = st.text_input("Default chat model", value=config.get("default_model", ""), key="def_model_txt")
        if st.button("Save", key="save_def_model_txt", use_container_width=True):
            _save("default_model", model.strip())
    base = st.text_input("Ollama base URL", value=config.get("ollama_base_url", "http://localhost:11434"), key="obase")
    if st.button("Save base URL", use_container_width=True):
        _save("ollama_base_url", base.strip())

    st.divider()
    st.subheader("Paths")
    with st.container(border=True):
        ws = st.text_input(
            "Workspace dir (file tools sandbox)",
            value=config.get("workspace_dir", ""), key="ws_dir",
            placeholder="default: data/workspace",
        )
        vd = st.text_input(
            "Obsidian vault dir (RAG)",
            value=config.get("vault_dir", ""), key="vault_dir",
            placeholder="default: env OBSIDIAN_VAULT or ~/brain",
        )
        kd = st.text_input(
            "Knowledge base dir (nightly-learn)",
            value=config.get("knowledge_dir", ""), key="knowledge_dir",
            placeholder="default: deepseek-cave/nightly-learn/knowledge",
        )
        bd = st.text_input(
            "Backup dir (local snapshots)",
            value=config.get("backup_dir", ""), key="bk_dir",
            placeholder="default: env BACKUP_DIR or ./backups",
        )
        sq = st.text_input(
            "SQLite DB for query_sql",
            value=config.get("sqlite_db", ""), key="sqlite_db",
            placeholder="default: data/assistant.db",
        )
        sk = st.text_input(
            "Skills dir",
            value=config.get("skills_dir", ""), key="skills_dir",
            placeholder="default: data/skills",
        )
        keep = st.number_input("Backups to keep", min_value=1, max_value=50, step=1,
                               value=int(config.get("backup_retention", 10)), key="bk_keep")
        if st.button("💾 Save paths", use_container_width=True):
            _save("workspace_dir", ws.strip())
            _save("vault_dir", vd.strip())
            _save("knowledge_dir", kd.strip())
            _save("backup_dir", bd.strip())
            _save("sqlite_db", sq.strip())
            _save("skills_dir", sk.strip())
            _save("backup_retention", int(keep))

    st.divider()
    st.subheader("MCP servers")
    st.caption(
        "External tool servers (nanobot/private-gpt research): a JSON list of "
        "{name, command, args}. Example: "
        "`[{\"name\": \"fs\", \"command\": \"npx\", \"args\": [\"-y\", "
        "\"@modelcontextprotocol/server-filesystem\", \"C:/path\"}]`"
    )
    mcp_val = st.text_area(
        "mcp_servers (JSON)",
        value=json.dumps(config.get("mcp_servers", []), indent=2),
        height=160, key="mcp_servers",
    )
    if st.button("💾 Save MCP servers", use_container_width=True):
        try:
            parsed = json.loads(mcp_val or "[]")
            if not isinstance(parsed, list):
                raise ValueError("must be a JSON list")
            _save("mcp_servers", parsed)
        except Exception as e:
            st.error(f"Invalid JSON: {e}")

    st.divider()
    st.subheader("Reminder daemon")
    _daemon_toggle()

    st.divider()
    st.subheader("Python session")
    st.caption(f"🟢 Running" if repl.is_running() else "⚪ Not started")
    if st.button("Restart Python session", use_container_width=True):
        st.toast(repl.restart())
        st.rerun()

# ---------------------------------------------------------------------------
# 💾 DATA
# ---------------------------------------------------------------------------
with tab_data:
    st.subheader("Memory")
    mem = memory.list_memory()
    if mem:
        for k, v in list(mem.items())[:50]:
            cols = st.columns([5, 1])
            cols[0].text(f"{k}: {v}")
            if cols[1].button("🗑", key=f"rmmem_{k}"):
                memory.forget(k)
                st.rerun()
        if len(mem) > 50:
            st.caption(f"...and {len(mem) - 50} more entries.")
    else:
        st.caption("Nothing remembered yet.")

    st.divider()
    st.subheader("Backups")
    backups = backup.list_backups()
    st.caption(f"{len(backups)} backup(s) at {backup.backup_location()}")
    for name in backups[:10]:
        cols = st.columns([5, 1])
        cols[0].text(name)
        if cols[1].button("↩️", key=f"restore_{name}", help="Restore this backup (destructive!)"):
            if st.session_state.get(f"confirm_{name}"):
                st.toast(backup.restore_backup(name))
                st.rerun()
            else:
                st.session_state[f"confirm_{name}"] = True
                st.warning(f"Click ↩️ again to confirm restoring {name} — current data will be replaced.")
    if st.button("💾 Back up now", use_container_width=True):
        st.toast(backup.create_backup())
        st.rerun()

    st.divider()
    st.subheader("Deep-analysis cache")
    if st.button("🧹 Clear crew cache", use_container_width=True):
        st.toast(crew_cache.clear())
        st.rerun()

    st.divider()
    st.subheader("Configuration file")
    st.caption(f"Stored at {config.CONFIG_FILE}")
    if config.CONFIG_FILE.exists():
        st.code(config.CONFIG_FILE.read_text(encoding="utf-8"), language="json")
    if st.button("↩️ Reset all settings to defaults", use_container_width=True):
        st.toast(config.reset())
        st.rerun()

st.caption("---")
st.caption(
    "Tip: path and model changes apply to the next call; restart the app "
    "(`streamlit run app.py`) to be fully safe. Run "
    "`python -m pytest tests/ -q` after big changes to verify nothing broke."
)
