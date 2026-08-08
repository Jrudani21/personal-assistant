"""Personal AI Assistant — local, free, runs on Ollama. Streamlit UI."""
import hashlib

import streamlit as st
import ollama

from assistant.llm import stream_chat
from assistant import memory, sessions, rag, voice

st.set_page_config(page_title="Personal Assistant", page_icon="🧠", layout="centered")

AVAILABLE_MODELS = [m["model"] for m in ollama.list().get("models", [])]
DEFAULT_MODEL = "qwen2.5:7b" if "qwen2.5:7b" in AVAILABLE_MODELS else (
    AVAILABLE_MODELS[0] if AVAILABLE_MODELS else None
)

if "chat" not in st.session_state:
    st.session_state.chat = sessions.new_chat()

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
        tool_box = st.container()
        tool_log = []

        def on_tool_call(name, args, result):
            tool_log.append(f"🔧 `{name}({args})` → {str(result)[:200]}")
            tool_box.info("\n\n".join(tool_log))

        reply = st.write_stream(
            stream_chat(model, st.session_state.chat["messages"], on_tool_call=on_tool_call)
        )

        if speak_replies and reply.strip():
            with st.spinner("Synthesizing speech..."):
                wav_bytes = voice.speak(reply)
            st.audio(wav_bytes, format="audio/wav", autoplay=True)

    st.session_state.chat["messages"].append({"role": "assistant", "content": reply})
    sessions.save(st.session_state.chat)
