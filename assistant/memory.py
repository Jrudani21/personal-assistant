"""Persistent key-value memory for the assistant, stored as local JSON.

Values are stored as {"value": ..., "updated_at": <iso>} so the system
prompt can inject only the most recently updated facts (injecting every
fact ever stored would grow the prompt without bound and crowd a small
local model — see brain/notes on prompt crowding). Legacy plain-string
values from before this change are still read correctly.
"""
import datetime
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


def _coerce(value) -> str:
    """Unwrap {'value': ...} entries; pass legacy plain strings through."""
    if isinstance(value, dict) and "value" in value:
        return str(value["value"])
    return str(value)


def _updated_at(value) -> str:
    """Sort key: stored timestamp, or epoch for legacy string entries."""
    if isinstance(value, dict) and "updated_at" in value:
        return value["updated_at"]
    return "1970-01-01T00:00:00"


def remember(key: str, value: str) -> str:
    data = _load()
    data[key] = {
        "value": value,
        "updated_at": datetime.datetime.now().isoformat(timespec="microseconds"),
    }
    _save(data)
    return f"Remembered '{key}'."


def recall(key: str) -> str:
    data = _load()
    if key in data:
        return _coerce(data[key])
    return f"Nothing stored under '{key}'."


def list_memory() -> dict:
    """All facts as {key: value-string} for display (UI sidebar, prompts)."""
    return {k: _coerce(v) for k, v in _load().items()}


def recent(limit: int = 30) -> list[tuple[str, str]]:
    """The most recently updated facts, newest first, capped at `limit`.
    Used by the system prompt so memory stays bounded under prompt crowding."""
    items = sorted(_load().items(), key=lambda kv: _updated_at(kv[1]), reverse=True)
    return [(k, _coerce(v)) for k, v in items[:limit]]


def forget(key: str) -> str:
    data = _load()
    if key in data:
        del data[key]
        _save(data)
        return f"Forgot '{key}'."
    return f"Nothing stored under '{key}'."
