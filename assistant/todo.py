"""Persistent task scratchpad for the assistant, stored as local JSON."""
import json
from pathlib import Path

TODO_FILE = Path(__file__).resolve().parent.parent / "data" / "todo.json"


def _load() -> list[dict]:
    if not TODO_FILE.exists():
        return []
    return json.loads(TODO_FILE.read_text(encoding="utf-8"))


def _save(data: list[dict]) -> None:
    TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
    TODO_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def add_task(text: str) -> str:
    data = _load()
    task_id = (max((t["id"] for t in data), default=0)) + 1
    data.append({"id": task_id, "text": text, "done": False})
    _save(data)
    return f"Added task #{task_id}: {text}"


def list_tasks() -> str:
    data = _load()
    if not data:
        return "No tasks."
    lines = [f"#{t['id']} [{'x' if t['done'] else ' '}] {t['text']}" for t in data]
    return "\n".join(lines)


def get_tasks() -> list[dict]:
    return _load()


def complete_task(task_id: int) -> str:
    return set_task_done(task_id, True)


def set_task_done(task_id: int, done: bool) -> str:
    data = _load()
    for t in data:
        if t["id"] == task_id:
            t["done"] = done
            _save(data)
            return f"{'Completed' if done else 'Reopened'} task #{task_id}."
    return f"No task #{task_id}."


def delete_task(task_id: int) -> str:
    data = _load()
    kept = [t for t in data if t["id"] != task_id]
    if len(kept) == len(data):
        return f"No task #{task_id}."
    _save(kept)
    return f"Deleted task #{task_id}."


def clear_tasks() -> str:
    _save([])
    return "Cleared all tasks."
