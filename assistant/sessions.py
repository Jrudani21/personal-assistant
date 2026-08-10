"""Persisted multi-chat sessions.

Storage format (nanobot's append-only JSONL pattern — see
brain/notes/Memory system design study.md):
- `<id>.jsonl`    — append-only message log. One JSON line per message:
                    {"role": ..., "content": ...}. O(1) crash-safe appends;
                    never rewritten for the common turn (the per-turn full
                    rewrite the old format did is gone).
- `<id>.meta.json` — tiny sidecar: {id, title, created_at, updated_at,
                    message_count, compaction}. Rewritten only when the
                    title/compaction cache changes (rare), never per turn.

Backward compatible: legacy `<id>.json` chats (the old full-array format)
still load, and migrate to the new format on their first save.
"""
import json
import uuid
from pathlib import Path

CHATS_DIR = Path(__file__).resolve().parent.parent / "data" / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)

MAX_TITLE_CHARS = 48

_FILLER_PREFIXES = (
    "hey ", "hi ", "hello ", "yo ", "please ", "plz ",
    "can you ", "could you ", "would you ", "will you ", "do you ",
    "i want to ", "i wanna ", "i need to ", "i'd like to ", "i would like to ",
    "how do i ", "how can i ", "how to ",
    "tell me ", "show me ", "help me ",
)


def _make_title(text: str) -> str:
    """Turn the first user message into a short, readable chat title:
    collapse whitespace, drop greeting/filler prefixes, keep the first
    sentence, and truncate at a word boundary."""
    t = " ".join((text or "").split()).strip()
    lowered = t.lower()
    for prefix in _FILLER_PREFIXES:
        if lowered.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    for end in (".", "!", "?"):
        idx = t.find(end)
        if idx != -1:
            t = t[:idx + 1].strip()
            break
    if t:
        t = t[0].upper() + t[1:]
    if len(t) > MAX_TITLE_CHARS:
        cut = t.rfind(" ", 0, MAX_TITLE_CHARS)
        t = (t[:cut] if cut > 20 else t[:MAX_TITLE_CHARS]).rstrip() + "…"
    return t or "New chat"


def _jsonl_path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.jsonl"


def _meta_path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.meta.json"


def _legacy_path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.json"


def new_chat() -> dict:
    return {"id": uuid.uuid4().hex[:12], "title": "New chat", "messages": []}


# ---------- low-level I/O ----------

def _read_jsonl(path: Path) -> list[dict]:
    messages = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                    messages.append(msg)
    except FileNotFoundError:
        pass
    return messages


def _read_meta(chat_id: str) -> dict:
    try:
        return json.loads(_meta_path(chat_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_meta(meta: dict) -> None:
    _meta_path(meta["id"]).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _append_messages(chat_id: str, messages: list[dict]) -> None:
    with open(_jsonl_path(chat_id), "a", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps({"role": m["role"], "content": m["content"]}, ensure_ascii=False) + "\n")


def _migrate_legacy(chat_id: str) -> dict | None:
    """If an old-format <id>.json exists, convert it to jsonl + meta and
    delete the legacy file. Returns the migrated chat dict, or None."""
    legacy = _legacy_path(chat_id)
    if not legacy.exists():
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    messages = data.get("messages", [])
    meta = {
        "id": chat_id,
        "title": data.get("title", "New chat"),
        "created_at": data.get("created_at", ""),
        "updated_at": "",
        "message_count": len(messages),
        "compaction": data.get("compaction"),
    }
    _append_messages(chat_id, messages)
    _write_meta(meta)
    legacy.unlink(missing_ok=True)
    return {"id": chat_id, "title": meta["title"], "messages": messages,
            "compaction": meta["compaction"]}


def _load_chat(chat_id: str) -> dict | None:
    """Load a chat from either storage format. Returns None if unknown."""
    if _jsonl_path(chat_id).exists() or _meta_path(chat_id).exists():
        meta = _read_meta(chat_id)
        messages = _read_jsonl(_jsonl_path(chat_id))
        return {
            "id": chat_id,
            "title": meta.get("title", "New chat"),
            "messages": messages,
            "compaction": meta.get("compaction"),
        }
    migrated = _migrate_legacy(chat_id)
    if migrated is not None:
        return migrated
    return None


# ---------- public API ----------

def save(chat: dict) -> None:
    """Persist a chat: append any new messages to the JSONL (O(1)) and update
    the tiny meta sidecar. Legacy chats are migrated on first save."""
    if chat["messages"] and chat["title"] == "New chat":
        first_user = next((m["content"] for m in chat["messages"] if m["role"] == "user"), "")
        chat["title"] = _make_title(first_user)

    chat_id = chat["id"]
    _migrate_legacy(chat_id)

    meta = _read_meta(chat_id)
    if not meta:
        meta = {"id": chat_id, "title": chat.get("title", "New chat"),
                "created_at": "", "updated_at": "", "message_count": 0,
                "compaction": None}
    if not meta.get("created_at"):
        meta["created_at"] = meta.get("created_at") or ""
    meta["title"] = chat.get("title", meta.get("title", "New chat"))
    meta["updated_at"] = meta.get("updated_at") or ""
    if "compaction" in chat:
        meta["compaction"] = chat["compaction"]

    persisted = meta.get("message_count", 0)
    messages = chat["messages"]
    if len(messages) > persisted:
        _append_messages(chat_id, messages[persisted:])
        meta["message_count"] = len(messages)
    elif len(messages) < persisted:
        # shouldn't happen (messages are append-only), but heal by rewriting
        tmp = _jsonl_path(chat_id).with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps({"role": m["role"], "content": m["content"]}, ensure_ascii=False) + "\n")
        tmp.replace(_jsonl_path(chat_id))
        meta["message_count"] = len(messages)
    _write_meta(meta)


def load(chat_id: str) -> dict:
    chat = _load_chat(chat_id)
    if chat is None:
        raise FileNotFoundError(f"No chat {chat_id}")
    return chat


def delete(chat_id: str) -> None:
    for p in (_jsonl_path(chat_id), _meta_path(chat_id), _legacy_path(chat_id)):
        p.unlink(missing_ok=True)


def export_markdown(chat: dict) -> str:
    lines = [f"# {chat.get('title', 'Chat')}", ""]
    for m in chat["messages"]:
        role = "**You**" if m["role"] == "user" else "**Assistant**"
        lines.append(f"{role}: {m['content']}")
        lines.append("")
    return "\n".join(lines)


def _chat_files() -> list[Path]:
    """All chat storage files (jsonl + legacy json), newest mtime first."""
    files = sorted(
        list(CHATS_DIR.glob("*.jsonl")) + list(CHATS_DIR.glob("*.json")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    # exclude meta sidecars and tmp files
    return [p for p in files
            if not p.name.endswith(".meta.json") and not p.name.endswith(".tmp")]


def search_chats(query: str) -> list[dict]:
    """Case-insensitive search over chat titles and message content.
    Returns matches newest-first with a short snippet of the hit."""
    q = query.lower().strip()
    if not q:
        return list_chats()
    results = []
    for f in _chat_files():
        chat_id = f.stem
        chat = _load_chat(chat_id)
        if chat is None:
            continue
        title = chat.get("title", "New chat")
        snippet = None
        if q in title.lower():
            snippet = title
        else:
            for m in chat.get("messages", []):
                content = m.get("content", "")
                idx = content.lower().find(q)
                if idx != -1:
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(q) + 40)
                    snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
                    break
        if snippet is not None:
            results.append({"id": chat_id, "title": title, "snippet": snippet})
    return results


def list_chats() -> list[dict]:
    """Newest first, by file mtime."""
    out = []
    for f in _chat_files():
        chat = _load_chat(f.stem)
        if chat is None:
            continue
        out.append({"id": chat["id"], "title": chat.get("title", "New chat")})
    return out
