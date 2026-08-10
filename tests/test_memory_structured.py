"""Tests for structured memory (facts + concepts) and observation capture."""
import json

from assistant import memory, observations


# ---------- structured memory ----------

def test_remember_with_facts_and_concepts(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("hardware", "RTX 4060", facts=["GPU: RTX 4060 8 GB"], concepts=["hardware", "machine"])
    entry = memory.get_entry("hardware")
    assert entry["value"] == "RTX 4060"
    assert entry["facts"] == ["GPU: RTX 4060 8 GB"]
    assert entry["concepts"] == ["hardware", "machine"]
    assert entry["updated_at"]


def test_remember_without_facts_defaults_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("plain", "just a value")
    entry = memory.get_entry("plain")
    assert entry["value"] == "just a value"
    assert entry["facts"] == []
    assert entry["concepts"] == []


def test_get_entry_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    assert memory.get_entry("nope") is None


def test_legacy_plain_string_still_reads(tmp_path, monkeypatch):
    f = tmp_path / "memory.json"
    f.write_text('{"old": "plain string"}', encoding="utf-8")
    monkeypatch.setattr(memory, "MEMORY_FILE", f)
    entry = memory.get_entry("old")
    assert entry["value"] == "plain string"
    assert entry["facts"] == [] and entry["concepts"] == []


def test_legacy_value_dict_without_facts_still_reads(tmp_path, monkeypatch):
    f = tmp_path / "memory.json"
    f.write_text('{"old": {"value": "v", "updated_at": "2026-01-01T00:00:00"}}', encoding="utf-8")
    monkeypatch.setattr(memory, "MEMORY_FILE", f)
    entry = memory.get_entry("old")
    assert entry["value"] == "v"
    assert entry["facts"] == [] and entry["concepts"] == []


def test_recall_and_list_memory_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("a", "1", facts=["fact a"], concepts=["tag"])
    assert memory.recall("a") == "1"
    assert memory.list_memory() == {"a": "1"}


def test_recent_entries_include_facts_and_sort_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("first", "1", facts=["old fact"])
    memory.remember("second", "2", facts=["new fact"], concepts=["x"])
    entries = memory.recent_entries(limit=10)
    assert [e["key"] for e in entries] == ["second", "first"]
    assert entries[0]["facts"] == ["new fact"]
    assert entries[0]["concepts"] == ["x"]
    assert entries[1]["facts"] == ["old fact"]


def test_recent_entries_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    for i in range(5):
        memory.remember(f"k{i}", str(i))
    assert len(memory.recent_entries(limit=2)) == 2
    assert memory.recent_entries(limit=2)[0]["key"] == "k4"


# ---------- observation capture ----------

def test_observations_append_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    observations.append("calculator", {"expression": "1+1"}, "2")
    observations.append("web_search", {"query": "ollama"}, "some results")
    entries = observations.read()
    assert len(entries) == 2
    assert entries[0]["tool"] == "calculator"
    assert entries[0]["args"] == '{"expression": "1+1"}'
    assert entries[0]["result"] == "2"
    assert entries[0]["args_truncated"] is False
    assert entries[1]["tool"] == "web_search"


def test_observations_read_returns_newest_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    for i in range(5):
        observations.append(f"tool{i}", {}, str(i))
    entries = observations.read(limit=2)
    assert [e["tool"] for e in entries] == ["tool3", "tool4"]


def test_observations_truncate_long_args_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    long_arg = "x" * (observations.ARG_CHARS + 100)
    long_result = "y" * (observations.RESULT_CHARS + 100)
    observations.append("write_file", {"content": long_arg}, long_result)
    entry = observations.read()[0]
    assert entry["args_truncated"] is True
    assert entry["result_truncated"] is True
    assert len(entry["args"]) <= observations.ARG_CHARS + 50
    assert len(entry["result"]) <= observations.RESULT_CHARS + 50
    assert "more chars]" in entry["args"]
    assert "more chars]" in entry["result"]


def test_observations_count_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    assert observations.count() == 0
    observations.append("a", {}, "1")
    observations.append("b", {}, "2")
    assert observations.count() == 2
    assert "Cleared" in observations.clear()
    assert observations.count() == 0


def test_observations_compacts_when_file_large(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    monkeypatch.setattr(observations, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(observations, "KEEP_ENTRIES", 3)
    for i in range(20):
        observations.append(f"tool{i}", {"payload": "z" * 50}, "result")
    # compaction happened: far fewer than the 20 appended lines survive,
    # the newest entries are retained, and the file is bounded
    assert observations.count() < 10
    entries = observations.read()
    assert entries[-1]["tool"] == "tool19"
    assert observations.OBSERVATIONS_FILE.stat().st_size < 2000


def test_observations_append_never_raises_on_bad_path(tmp_path, monkeypatch):
    # even if the file can't be written (e.g. path is a directory), the chat
    # must not break — observation capture is best-effort
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path)  # a directory
    observations.append("calculator", {}, "2")  # should not raise


def test_observations_skips_corrupt_lines(tmp_path, monkeypatch):
    f = tmp_path / "obs.jsonl"
    f.write_text('{"tool": "good"}\nnot json\n{"tool": "also good"}\n', encoding="utf-8")
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", f)
    entries = observations.read()
    assert [e["tool"] for e in entries] == ["good", "also good"]
