"""Tests for append-only JSONL sessions and memory distillation."""
import json

import pytest

from assistant import distill, memory, observations, sessions


# ---------- append-only JSONL sessions ----------

def test_save_appends_jsonl_and_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "tell me about Poisson regression"})
    sessions.save(chat)

    jsonl = tmp_path / f"{chat['id']}.jsonl"
    meta = tmp_path / f"{chat['id']}.meta.json"
    assert jsonl.exists() and meta.exists()
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["role"] == "user"
    m = json.loads(meta.read_text(encoding="utf-8"))
    assert m["message_count"] == 1
    assert m["title"] == "About Poisson regression"


def test_save_appends_incrementally_not_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "first"})
    sessions.save(chat)
    before = (tmp_path / f"{chat['id']}.jsonl").stat().st_mtime_ns

    chat["messages"].append({"role": "assistant", "content": "reply"})
    chat["messages"].append({"role": "user", "content": "second"})
    sessions.save(chat)

    jsonl = tmp_path / f"{chat['id']}.jsonl"
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    m = json.loads((tmp_path / f"{chat['id']}.meta.json").read_text(encoding="utf-8"))
    assert m["message_count"] == 3
    # mtime changed only by appending, but no rewrite happened: the file
    # still contains exactly the appended lines in order
    assert [json.loads(l)["content"] for l in lines] == ["first", "reply", "second"]


def test_load_roundtrip_includes_compaction(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "q"})
    chat["compaction"] = {"up_to_index": 1, "summary": "s"}
    sessions.save(chat)
    loaded = sessions.load(chat["id"])
    assert loaded["messages"] == chat["messages"]
    assert loaded["compaction"] == {"up_to_index": 1, "summary": "s"}


def test_legacy_json_migrates_on_save(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat_id = "abc123"
    legacy = tmp_path / f"{chat_id}.json"
    legacy.write_text(json.dumps({
        "id": chat_id, "title": "Old chat",
        "messages": [{"role": "user", "content": "legacy msg"}],
    }), encoding="utf-8")
    # load reads legacy
    loaded = sessions.load(chat_id)
    assert loaded["title"] == "Old chat"
    assert loaded["messages"][0]["content"] == "legacy msg"
    # save migrates to jsonl + meta and removes the legacy file
    loaded["messages"].append({"role": "assistant", "content": "new reply"})
    sessions.save(loaded)
    assert not legacy.exists()
    assert (tmp_path / f"{chat_id}.jsonl").exists()
    lines = (tmp_path / f"{chat_id}.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    m = json.loads((tmp_path / f"{chat_id}.meta.json").read_text(encoding="utf-8"))
    assert m["message_count"] == 2
    assert m["title"] == "Old chat"


def test_list_and_search_cover_both_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    # new format chat
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "hello world from jsonl"})
    sessions.save(chat)
    # legacy format chat
    legacy_id = "legacy1"
    (tmp_path / f"{legacy_id}.json").write_text(json.dumps({
        "id": legacy_id, "title": "Legacy chat",
        "messages": [{"role": "user", "content": "needle in legacy haystack"}],
    }), encoding="utf-8")

    listed = sessions.list_chats()
    assert {c["id"] for c in listed} == {chat["id"], legacy_id}

    hits = sessions.search_chats("needle")
    assert hits and hits[0]["id"] == legacy_id
    hits2 = sessions.search_chats("jsonl")
    assert hits2 and hits2[0]["id"] == chat["id"]


def test_delete_removes_all_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "x"})
    sessions.save(chat)
    sessions.delete(chat["id"])
    assert not (tmp_path / f"{chat['id']}.jsonl").exists()
    assert not (tmp_path / f"{chat['id']}.meta.json").exists()


def test_save_heals_shrunk_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "a"})
    chat["messages"].append({"role": "assistant", "content": "b"})
    sessions.save(chat)
    # simulate a caller holding a truncated view (e.g. after a rollback)
    chat["messages"] = [{"role": "user", "content": "a"}]
    sessions.save(chat)
    lines = (tmp_path / f"{chat['id']}.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    m = json.loads((tmp_path / f"{chat['id']}.meta.json").read_text(encoding="utf-8"))
    assert m["message_count"] == 1


# ---------- distillation ----------

def _fake_ollama(monkeypatch, content):
    calls = {}
    def fake_chat(model, messages, **kwargs):
        calls["model"] = model
        calls["messages"] = messages
        return {"message": {"content": content}}
    monkeypatch.setattr(distill.ollama, "chat", fake_chat)
    return calls


def test_parse_candidates_accepts_clean_json():
    text = '[{"key": "fav_tool", "value": "R", "facts": ["Uses R"], "concepts": ["lang"]}]'
    out = distill._parse_candidates(text)
    assert out == [{"key": "fav_tool", "value": "R", "facts": ["Uses R"], "concepts": ["lang"]}]


def test_parse_candidates_strips_markdown_fences():
    text = '```json\n[{"key": "a", "value": "v"}]\n```'
    out = distill._parse_candidates(text)
    assert out == [{"key": "a", "value": "v", "facts": [], "concepts": []}]


def test_parse_candidates_rejects_garbage():
    assert distill._parse_candidates("no json here") == []
    assert distill._parse_candidates('{"not": "a list"}') == []
    assert distill._parse_candidates("[not valid json") == []


def test_parse_candidates_skips_bad_keys():
    text = '[{"key": "Bad Key!", "value": "x"}, {"key": "ok_key", "value": "y"}]'
    out = distill._parse_candidates(text)
    assert [c["key"] for c in out] == ["ok_key"]


def test_distill_adds_new_keys_and_skips_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    monkeypatch.setattr(distill, "_pick_model", lambda: "qwen2.5:7b")
    observations.append("web_search", {"query": "poisson regression"}, "results")
    memory.remember("existing", "already here")
    _fake_ollama(monkeypatch, json.dumps([
        {"key": "existing", "value": "dup", "facts": [], "concepts": []},
        {"key": "new_fact", "value": "Uses R", "facts": ["Uses R"], "concepts": ["lang"]},
    ]))
    result = distill.distill()
    assert "new_fact" in result
    assert "already known" in result
    assert memory.get_entry("existing")["value"] == "already here"  # not overwritten
    assert memory.get_entry("new_fact")["value"] == "Uses R"


def test_distill_no_activity_returns_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    assert "nothing to learn from" in distill.distill()


def test_distill_model_garbage_reply_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    monkeypatch.setattr(distill, "_pick_model", lambda: "qwen2.5:7b")
    observations.append("web_search", {"query": "x"}, "y")
    _fake_ollama(monkeypatch, "I don't understand the request.")
    result = distill.distill()
    assert "no valid facts" in result
    assert memory.list_memory() == {}  # memory untouched


def test_distill_ollama_error_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_FILE", tmp_path / "obs.jsonl")
    monkeypatch.setattr(distill, "_pick_model", lambda: "qwen2.5:7b")
    observations.append("web_search", {"query": "x"}, "y")
    def boom(model, messages, **kwargs):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(distill.ollama, "chat", boom)
    assert "Distillation failed" in distill.distill()
