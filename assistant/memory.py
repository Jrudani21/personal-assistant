"""Persistent key-value memory for the assistant, stored as local JSON."""
import json
from pathlib import Path

MEMORY_FILE = Path(__file__).resolve().parent.parent / "data" / "memory.json"


def _load() -> dict:
    if not MEMORY_FILE.exists():
        return {}
    return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remember(key: str, value: str) -> str:
    data = _load()
    data[key] = value
    _save(data)
    return f"Remembered '{key}'."


def recall(key: str) -> str:
    data = _load()
    if key in data:
        return data[key]
    return f"Nothing stored under '{key}'."


def list_memory() -> dict:
    return _load()


def forget(key: str) -> str:
    data = _load()
    if key in data:
        del data[key]
        _save(data)
        return f"Forgot '{key}'."
    return f"Nothing stored under '{key}'."
