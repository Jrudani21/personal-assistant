import datetime
import json

import pytest

from assistant import crew_cache


@pytest.fixture(autouse=True)
def _clean_cache_file():
    crew_cache.clear()
    yield
    crew_cache.clear()


def test_store_and_get_roundtrip():
    crew_cache.store("compare Poisson and SARIMA", "the report", used_fallback=False)
    assert crew_cache.get_cached("compare Poisson and SARIMA") == "the report"


def test_key_is_whitespace_insensitive():
    crew_cache.store("Compare   Poisson and\nSARIMA", "the report", used_fallback=False)
    assert crew_cache.get_cached(" compare Poisson and SARIMA ") == "the report"


def test_missing_input_returns_none():
    assert crew_cache.get_cached("never analyzed") is None


def test_fallback_result_is_never_cached():
    crew_cache.store("topic", "a local-fallback report", used_fallback=True)
    assert crew_cache.get_cached("topic") is None


def test_error_result_is_never_cached():
    crew_cache.store("topic", "Deep analysis error: boom", used_fallback=False)
    assert crew_cache.get_cached("topic") is None


def test_empty_result_is_never_cached():
    crew_cache.store("topic", "   ", used_fallback=False)
    assert crew_cache.get_cached("topic") is None


def test_expired_entry_returns_none():
    crew_cache.store("topic", "old report", used_fallback=False)
    data = json.loads(crew_cache.CACHE_FILE.read_text(encoding="utf-8"))
    key = next(iter(data))
    data[key]["created_at"] = (datetime.datetime.now() - datetime.timedelta(days=8)).isoformat()
    crew_cache.CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    assert crew_cache.get_cached("topic") is None


def test_recent_entry_survives_ttl():
    crew_cache.store("topic", "fresh report", used_fallback=False)
    data = json.loads(crew_cache.CACHE_FILE.read_text(encoding="utf-8"))
    key = next(iter(data))
    data[key]["created_at"] = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
    crew_cache.CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    assert crew_cache.get_cached("topic") == "fresh report"


def test_cache_evicts_oldest_past_max_entries():
    for i in range(crew_cache.MAX_ENTRIES + 5):
        crew_cache.store(f"topic {i}", f"report {i}", used_fallback=False)
    data = json.loads(crew_cache.CACHE_FILE.read_text(encoding="utf-8"))
    assert len(data) <= crew_cache.MAX_ENTRIES
    # the five oldest ("topic 0".."topic 4") must be gone
    for i in range(5):
        assert crew_cache.get_cached(f"topic {i}") is None
    assert crew_cache.get_cached(f"topic {crew_cache.MAX_ENTRIES + 4}") is not None


def test_clear_removes_all():
    crew_cache.store("a", "one", used_fallback=False)
    crew_cache.store("b", "two", used_fallback=False)
    crew_cache.clear()
    assert crew_cache.get_cached("a") is None
    assert crew_cache.get_cached("b") is None


def test_corrupt_cache_file_returns_none(monkeypatch):
    crew_cache.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    crew_cache.CACHE_FILE.write_text("{not json", encoding="utf-8")
    assert crew_cache.get_cached("anything") is None
    # and a store() still works, overwriting the corrupt file
    crew_cache.store("anything", "fresh", used_fallback=False)
    assert crew_cache.get_cached("anything") == "fresh"
