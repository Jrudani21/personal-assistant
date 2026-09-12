"""Caching for deep_analysis results.

Re-running the same topic pays the full four-agent pipeline cost (~45-120s)
every time. This cache makes an identical (whitespace-insensitive) input
return the stored report instantly.

Two deliberate rules:
- Only results produced by a cloud analysis step are cached. A
  local-fallback result can misstate numeric comparisons (measured ~1 in 3
  runs), so it is never cached — a wrong cached answer wearing the authority
  of a verified analysis would be worse than no cache at all.
- Failures are never cached.
"""
import datetime
import hashlib
import json
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "crew_cache.json"
DEFAULT_TTL_DAYS = 7
MAX_ENTRIES = 50


def _normalize(raw_input: str) -> str:
    """Whitespace- and case-insensitive keying:
    'Compare   A and B' == 'compare a and b'."""
    return " ".join(raw_input.split()).strip().lower()


def _key(raw_input: str) -> str:
    return hashlib.sha256(_normalize(raw_input).encode("utf-8")).hexdigest()


def _load() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}  # corrupt cache is not worth crashing a deep-analysis run over


def _save(data: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_cached(raw_input: str, ttl_days: int = DEFAULT_TTL_DAYS) -> str | None:
    """Return the cached report for raw_input, or None if absent/expired."""
    entry = _load().get(_key(raw_input))
    if not entry:
        return None
    try:
        created = datetime.datetime.fromisoformat(entry["created_at"])
    except (KeyError, ValueError):
        return None
    if datetime.datetime.now() - created > datetime.timedelta(days=ttl_days):
        return None
    return entry["result"]


def store(raw_input: str, result: str, used_fallback: bool) -> None:
    """Cache a successful non-fallback result. No-ops for fallback runs,
    empty results, and anything that looks like an error message."""
    if used_fallback:
        return
    result = (result or "").strip()
    if not result or result.startswith("Deep analysis error:"):
        return
    data = _load()
    now = datetime.datetime.now().isoformat()
    data[_key(raw_input)] = {
        "input": _normalize(raw_input),
        "result": result,
        "created_at": now,
    }
    if len(data) > MAX_ENTRIES:
        oldest = sorted(data.items(), key=lambda kv: kv[1]["created_at"])
        for key, _ in oldest[: len(data) - MAX_ENTRIES]:
            data.pop(key, None)
    _save(data)


def clear() -> str:
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    return "Cleared the deep-analysis cache."
