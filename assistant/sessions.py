"""Persisted multi-chat sessions, one JSON file per conversation."""
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


def _path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.json"


def new_chat() -> dict:
    return {"id": uuid.uuid4().hex[:12], "title": "New chat", "messages": []}


def save(chat: dict) -> None:
    if chat["messages"] and chat["title"] == "New chat":
        first_user = next((m["content"] for m in chat["messages"] if m["role"] == "user"), "")
        chat["title"] = _make_title(first_user)
    _path(chat["id"]).write_text(json.dumps(chat, indent=2), encoding="utf-8")


def load(chat_id: str) -> dict:
    return json.loads(_path(chat_id).read_text(encoding="utf-8"))


def delete(chat_id: str) -> None:
    p = _path(chat_id)
    if p.exists():
        p.unlink()


def export_markdown(chat: dict) -> str:
    lines = [f"# {chat.get('title', 'Chat')}", ""]
    for m in chat["messages"]:
        role = "**You**" if m["role"] == "user" else "**Assistant**"
        lines.append(f"{role}: {m['content']}")
        lines.append("")
    return "\n".join(lines)


def search_chats(query: str) -> list[dict]:
    """Case-insensitive search over chat titles and message content.
    Returns matches newest-first with a short snippet of the hit."""
    q = query.lower().strip()
    if not q:
        return list_chats()
    files = sorted(CHATS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        title = data.get("title", "New chat")
        snippet = None
        if q in title.lower():
            snippet = title
        else:
            for m in data.get("messages", []):
                content = m.get("content", "")
                idx = content.lower().find(q)
                if idx != -1:
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(q) + 40)
                    snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
                    break
        if snippet is not None:
            results.append({"id": data["id"], "title": title, "snippet": snippet})
    return results


def list_chats() -> list[dict]:
    """Newest first, by file mtime."""
    files = sorted(CHATS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": data["id"], "title": data.get("title", "New chat")})
        except Exception:
            continue
    return out
