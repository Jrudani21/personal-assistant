"""Persistent structured memory for the assistant, stored as local JSON.

Each entry is:
    {key: {"value": str, "facts": [str], "concepts": [str], "updated_at": iso}}

`facts` are discrete, verifiable claims ("GPU: RTX 4060 8 GB"); `concepts`
are lightweight tags ("hardware", "statistics") that make retrieval and
dedup tractable later — the structured-memory idea from claude-mem
(see brain/notes/Memory system design study.md).

Backward compatible: legacy plain-string values and entries without
facts/concepts are read fine (defaulting to empty lists).
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


def _facts(value) -> list[str]:
    if isinstance(value, dict) and isinstance(value.get("facts"), list):
        return [str(f) for f in value["facts"]]
    return []


def _concepts(value) -> list[str]:
    if isinstance(value, dict) and isinstance(value.get("concepts"), list):
        return [str(c) for c in value["concepts"]]
    return []


def _updated_at(value) -> str:
    """Sort key: stored timestamp, or epoch for legacy string entries."""
    if isinstance(value, dict) and "updated_at" in value:
        return value["updated_at"]
    return "1970-01-01T00:00:00"


def remember(key: str, value: str, facts: list[str] | None = None,
             concepts: list[str] | None = None) -> str:
    """Save a fact/preference under `key`. `facts` are discrete claims,
    `concepts` are tags. Reusing an existing key overwrites it."""
    data = _load()
    data[key] = {
        "value": value,
        "facts": [str(f) for f in (facts or [])],
        "concepts": [str(c) for c in (concepts or [])],
        "updated_at": datetime.datetime.now().isoformat(timespec="microseconds"),
    }
    _save(data)
    return f"Remembered '{key}'."


def distill_merge(key: str, value: str, facts: list[str] | None = None,
                  concepts: list[str] | None = None, source: str = "") -> str:
    """ADD-ONLY merge for the distillation pipeline (Mem0-style, CoALA
    semantic tier). Unlike `remember`, this NEVER overwrites existing facts:
    new facts are appended, prior values are kept in a `history` list, and
    every entry carries provenance (`source` note + `distilled_at`).

    Contradictions are resolved at READ time by recency (research-backed
    2026-08-14) — the history preserves what was true when.
    """
    data = _load()
    now = datetime.datetime.now().isoformat(timespec="microseconds")
    existing = data.get(key, {})
    prev_value = _coerce(existing) if existing else None

    # Append-only history of prior values (bounded to last 20)
    history = list(existing.get("history", [])) if isinstance(existing, dict) else []
    if prev_value and prev_value != value:
        history.append({"value": prev_value, "as_of": _updated_at(existing)})
        history = history[-20:]

    merged_facts = list(_facts(existing))
    for f in (facts or []):
        if str(f) not in merged_facts:
            merged_facts.append(str(f))
    merged_concepts = list(set(_concepts(existing)) | set(concepts or []))

    data[key] = {
        "value": value,
        "facts": merged_facts,
        "concepts": merged_concepts,
        "history": history,
        "source": source,
        "distilled_at": now,
        "updated_at": now,
        "tier": "semantic",  # CoALA: distillation writes the semantic tier
    }
    _save(data)
    return f"Distilled '{key}' ({len(facts or [])} facts, source={source})."


# CoALA tiers (research-backed 2026-08-14): episodic = timestamped events
# (daily notes/session logs), semantic = durable de-duplicated facts
# (memory.json), procedural = skills/prompts/workflows (skills/ dir).
TIERS = ("episodic", "semantic", "procedural")


def set_tier(key: str, tier: str) -> str:
    """Tag an existing memory entry with its CoALA tier. No-op if missing."""
    if tier not in TIERS:
        return f"Unknown tier '{tier}' (use {TIERS})."
    data = _load()
    if key not in data:
        return f"Nothing stored under '{key}'."
    if isinstance(data[key], dict):
        data[key]["tier"] = tier
        _save(data)
        return f"'{key}' tagged {tier}."
    return f"'{key}' is a legacy string entry — use distill_merge to upgrade."


def get_entry(key: str) -> dict | None:
    """Full structured entry {value, facts, concepts, updated_at}, or None."""
    data = _load()
    if key not in data:
        return None
    value = data[key]
    return {
        "value": _coerce(value),
        "facts": _facts(value),
        "concepts": _concepts(value),
        "updated_at": _updated_at(value),
    }


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


def recent_entries(limit: int = 30) -> list[dict]:
    """Full structured entries, newest first, capped at `limit`. Richer than
    `recent` for prompt injection: includes facts and concepts when present."""
    items = sorted(_load().items(), key=lambda kv: _updated_at(kv[1]), reverse=True)
    out = []
    for k, v in items[:limit]:
        out.append({
            "key": k,
            "value": _coerce(v),
            "facts": _facts(v),
            "concepts": _concepts(v),
            "updated_at": _updated_at(v),
        })
    return out


def forget(key: str) -> str:
    data = _load()
    if key in data:
        del data[key]
        _save(data)
        return f"Forgot '{key}'."
    return f"Nothing stored under '{key}'."
