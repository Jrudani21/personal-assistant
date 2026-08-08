"""Persisted multi-chat sessions, one JSON file per conversation."""
import json
import uuid
from pathlib import Path

CHATS_DIR = Path(__file__).resolve().parent.parent / "data" / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)


def _path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.json"


def new_chat() -> dict:
    return {"id": uuid.uuid4().hex[:12], "title": "New chat", "messages": []}


def save(chat: dict) -> None:
    if chat["messages"] and chat["title"] == "New chat":
        first_user = next((m["content"] for m in chat["messages"] if m["role"] == "user"), "")
        chat["title"] = (first_user[:40] + "...") if len(first_user) > 40 else first_user or "New chat"
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
