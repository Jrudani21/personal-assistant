"""Personal AI Assistant — local, free, runs on Ollama. Streamlit UI."""
import hashlib

import streamlit as st
import ollama

from assistant.llm import stream_chat
from assistant import compaction, memory, sessions, rag, reminders, repl, todo, voice

st.set_page_config(page_title="Personal Assistant", page_icon="🧠", layout="centered")

st.markdown(
    """
    <style>
    .stApp { max-width: 900px; margin: 0 auto; }
    [data-testid="stChatMessage"] {
        border-radius: 14px;
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.4rem;
        box-shadow: 0 1px 2px rgba(0,0,0,0.06);
    }
    [data-testid="stSidebar"] { border-right: 1px solid rgba(128,128,128,0.15); }
    [data-testid="stSidebar"] .stButton button {
        border-radius: 8px;
        text-align: left;
        justify-content: flex-start;
    }
    div[data-testid="stDivider"] { margin: 0.6rem 0; }
    .empty-state {
        text-align: center;
        opacity: 0.6;
        padding: 3rem 1rem;
    }
    .empty-state h3 { margin-bottom: 0.3rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    AVAILABLE_MODELS = [m["model"] for m in ollama.list().get("models", [])]
except Exception as e:
    st.error(f"Can't reach Ollama: {e}\n\nMake sure it's running (`ollama serve`), then reload this page.")
    st.stop()
DEFAULT_MODEL = "qwen2.5:7b" if "qwen2.5:7b" in AVAILABLE_MODELS else (
    AVAILABLE_MODELS[0] if AVAILABLE_MODELS else None
)

if "chat" not in st.session_state:
    st.session_state.chat = sessions.new_chat()

toasted = st.session_state.setdefault("toasted_reminders", set())
for r in reminders.due_reminders():
    if r["id"] not in toasted:
        st.toast(f"⏰ {r['text']}", icon="⏰")
        toasted.add(r["id"])

# ---------- Sidebar ----------
with st.sidebar:
    st.title("🧠 Personal Assistant")
    st.caption("Local · Ollama · $0 cost")
    model = st.selectbox(
        "Model", AVAILABLE_MODELS,
        index=AVAILABLE_MODELS.index(DEFAULT_MODEL) if DEFAULT_MODEL else 0,
    )

    st.divider()
    if st.button("+ New chat", use_container_width=True):
        st.session_state.chat = sessions.new_chat()
        st.session_state.crew_result = None
        st.rerun()

    if st.session_state.chat["messages"]:
        st.download_button(
            "Export chat (.md)",
            sessions.export_markdown(st.session_state.chat),
            file_name=f"{st.session_state.chat['title'][:40] or 'chat'}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    search_q = st.text_input("Search chats", key="chat_search", placeholder="search...", label_visibility="collapsed")
    st.caption("Chats")
    chat_list = sessions.search_chats(search_q) if search_q.strip() else sessions.list_chats()
    if search_q.strip() and not chat_list:
        st.caption("No matches.")
    for c in chat_list:
        cols = st.columns([5, 1])
        active = c["id"] == st.session_state.chat["id"]
        if cols[0].button(("● " if active else "") + c["title"], key=f"load_{c['id']}", use_container_width=True):
            st.session_state.chat = sessions.load(c["id"])
            st.rerun()
        if c.get("snippet") and c["snippet"] != c["title"]:
            cols[0].caption(c["snippet"])
        if cols[1].button("🗑", key=f"del_{c['id']}"):
            sessions.delete(c["id"])
            if active:
                st.session_state.chat = sessions.new_chat()
            st.rerun()

    st.divider()
    st.subheader("Documents (RAG)")
    uploaded = st.file_uploader("Upload txt/md/pdf", type=["txt", "md", "pdf"], accept_multiple_files=True)
    if uploaded:
        for f in uploaded:
            with st.spinner(f"Ingesting {f.name}..."):
                msg = rag.ingest(f.name, f.getvalue())
            st.toast(msg)
    docs = rag.list_documents()
    if docs:
        for d in docs:
            dcols = st.columns([5, 1])
            dcols[0].caption(d)
            if dcols[1].button("🗑", key=f"rmdoc_{d}"):
                rag.remove_document(d)
                st.rerun()
    else:
        st.caption("No documents uploaded.")

    st.divider()
    st.subheader("Tasks")
    tasks = todo.get_tasks()
    if tasks:
        for t in tasks:
            tcols = st.columns([1, 4, 1])
            done = tcols[0].checkbox("", value=t["done"], key=f"task_{t['id']}", label_visibility="collapsed")
            if done != t["done"]:
                todo.set_task_done(t["id"], done)
                st.rerun()
            tcols[1].markdown(f"~~{t['text']}~~" if t["done"] else t["text"])
            if tcols[2].button("🗑", key=f"rmtask_{t['id']}"):
                todo.delete_task(t["id"])
                st.rerun()
    else:
        st.caption("No tasks.")

    st.divider()
    st.subheader("Reminders")
    pending = [r for r in reminders.get_reminders() if not r["fired"]]
    if pending:
        for r in pending:
            rcols = st.columns([5, 1])
            rcols[0].text(f"{r['due_at']} — {r['text']}")
            if rcols[1].button("🗑", key=f"rmrem_{r['id']}"):
                reminders.cancel_reminder(r["id"])
                st.rerun()
    else:
        st.caption("No pending reminders.")

    st.divider()
    st.subheader("Deep Analysis")
    st.caption("4-agent crew (web/wiki/files + real calc) + Claude Pro. ~45-120s.")
    crew_input = st.text_input(
        "Topic, question, or file path", key="crew_input",
        placeholder="e.g. Poisson vs SARIMA, or data/workspace/sales.csv",
    )
    if st.button("Run analysis", use_container_width=True, disabled=not crew_input.strip()):
        with st.spinner("Running crew (fetch -> verify -> analyze -> report)..."):
            from assistant import crew as crew_module
            st.session_state.crew_result = crew_module.run_deep_analysis(crew_input)
    if st.session_state.get("crew_result"):
        with st.expander("Last analysis result", expanded=True):
            st.markdown(st.session_state.crew_result)

    st.divider()
    st.subheader("Python session")
    st.caption("🟢 Running" if repl.is_running() else "⚪ Not started")
    if st.button("Restart Python session", use_container_width=True):
        st.toast(repl.restart())

    st.divider()
    st.subheader("Voice")
    speak_replies = st.checkbox("🔊 Speak replies", key="speak_replies")

    st.divider()
    st.subheader("Memory")
    mem = memory.list_memory()
    if mem:
        for k, v in mem.items():
            mcols = st.columns([5, 1])
            mcols[0].text(f"{k}: {v}")
            if mcols[1].button("🗑", key=f"rmmem_{k}"):
                memory.forget(k)
                st.rerun()
    else:
        st.caption("Nothing remembered yet.")

# ---------- Chat ----------
if not st.session_state.chat["messages"]:
    st.markdown(
        """
        <div class="empty-state">
        <h3>🧠 Ready when you are</h3>
        <p>Ask a question, upload a doc to search, or record a voice message.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

for msg in st.session_state.chat["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = None

with st.expander("🎙 Or record a voice message"):
    audio = st.audio_input("Record", label_visibility="collapsed")
    if audio is not None:
        audio_bytes = audio.getvalue()
        audio_hash = hashlib.md5(audio_bytes).hexdigest()
        if st.session_state.get("last_audio_hash") != audio_hash:
            st.session_state.last_audio_hash = audio_hash
            with st.spinner("Transcribing..."):
                prompt = voice.transcribe(audio_bytes)
            if not prompt:
                st.warning("Couldn't transcribe that — try again.")
                prompt = None

typed = st.chat_input("Ask me anything...")
if typed:
    prompt = typed

if prompt:
    if not DEFAULT_MODEL:
        st.error("No Ollama models found. Run `ollama pull qwen2.5:7b` first.")
        st.stop()

    st.session_state.chat["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        tool_box = st.empty()
        tool_log = []

        def on_tool_call(name, args, result):
            tool_log.append(f"🔧 `{name}({args})` → {str(result)[:200]}")
            with tool_box.expander(f"Used {len(tool_log)} tool{'s' if len(tool_log) != 1 else ''}", expanded=False):
                st.markdown("\n\n".join(tool_log))

        llm_messages = compaction.get_llm_messages(st.session_state.chat, model)
        reply = st.write_stream(
            stream_chat(model, llm_messages, on_tool_call=on_tool_call)
        )

        if speak_replies and reply.strip():
            with st.spinner("Synthesizing speech..."):
                wav_bytes = voice.speak(reply)
            st.audio(wav_bytes, format="audio/wav", autoplay=True)

    st.session_state.chat["messages"].append({"role": "assistant", "content": reply})
    sessions.save(st.session_state.chat)
