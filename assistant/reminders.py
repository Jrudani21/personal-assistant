"""Local reminders. No background daemon — checked on each Streamlit rerun,
so reminders surface while the app is open, not while it's closed."""
import datetime
import json
from pathlib import Path

REMINDERS_FILE = Path(__file__).resolve().parent.parent / "data" / "reminders.json"


def _load() -> list[dict]:
    if not REMINDERS_FILE.exists():
        return []
    return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))


def _save(data: list[dict]) -> None:
    REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remind_me(text: str, due_at: str) -> str:
    """due_at: ISO datetime string, e.g. '2026-08-09 14:00'."""
    try:
        parsed = datetime.datetime.fromisoformat(due_at)
    except ValueError:
        return f"Couldn't parse due_at '{due_at}'. Use ISO format like '2026-08-09 14:00'."
    data = _load()
    reminder_id = (max((r["id"] for r in data), default=0)) + 1
    data.append({"id": reminder_id, "text": text, "due_at": parsed.isoformat(), "fired": False})
    _save(data)
    return f"Reminder #{reminder_id} set for {parsed.isoformat()}: {text}"


def list_reminders() -> str:
    data = _load()
    if not data:
        return "No reminders."
    return "\n".join(f"#{r['id']} [{'fired' if r['fired'] else 'pending'}] {r['due_at']} — {r['text']}" for r in data)


def get_reminders() -> list[dict]:
    return _load()


def cancel_reminder(reminder_id: int) -> str:
    data = _load()
    kept = [r for r in data if r["id"] != reminder_id]
    if len(kept) == len(data):
        return f"No reminder #{reminder_id}."
    _save(kept)
    return f"Cancelled reminder #{reminder_id}."


def due_reminders() -> list[dict]:
    """Unfired reminders whose due_at has passed. Does not mark them fired."""
    now = datetime.datetime.now()
    return [r for r in _load() if not r["fired"] and datetime.datetime.fromisoformat(r["due_at"]) <= now]


def mark_fired(reminder_id: int) -> None:
    data = _load()
    for r in data:
        if r["id"] == reminder_id:
            r["fired"] = True
    _save(data)
