from assistant import memory
from assistant import sessions


# ---------- sessions._make_title ----------

def test_title_strips_filler_prefix():
    assert sessions._make_title("can you explain Poisson regression") == "Explain Poisson regression"


def test_title_keeps_plain_message():
    assert sessions._make_title("compare ARIMA and SARIMA") == "Compare ARIMA and SARIMA"


def test_title_takes_first_sentence():
    title = sessions._make_title("What's the weather in Winnipeg? And also in Toronto?")
    assert title == "What's the weather in Winnipeg?"


def test_title_collapses_whitespace():
    assert sessions._make_title("   check   the   data   ") == "Check the data"


def test_title_truncates_long_message_at_word_boundary():
    long = "a very long question about statistical models that keeps going " \
           "well past the maximum title length and should be cut"
    title = sessions._make_title(long)
    assert len(title) <= sessions.MAX_TITLE_CHARS + 1  # +1 for the ellipsis
    assert title.endswith("…")
    assert "maximum title length" not in title


def test_title_empty_message_falls_back():
    assert sessions._make_title("") == "New chat"
    assert sessions._make_title("   ") == "New chat"


def test_title_strips_one_leading_filler():
    # only the first filler layer is stripped — aggressive stripping of
    # "help me" would leave the meaningless stub "With R"
    assert sessions._make_title("hey can you help me with R") == "Can you help me with R"


# ---------- sessions.save title behavior ----------

def test_save_sets_title_only_for_new_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["messages"].append({"role": "user", "content": "can you list my files"})
    sessions.save(chat)
    assert chat["title"] == "List my files"


def test_save_does_not_overwrite_existing_title(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHATS_DIR", tmp_path)
    chat = sessions.new_chat()
    chat["title"] = "Custom title"
    chat["messages"].append({"role": "user", "content": "anything at all"})
    sessions.save(chat)
    assert chat["title"] == "Custom title"


# ---------- memory: timestamps, coercion, recency cap ----------

def test_remember_stores_value_and_recall_returns_string(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("favorite_language", "R")
    assert memory.recall("favorite_language") == "R"


def test_list_memory_unwraps_values(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("a", "1")
    memory.remember("b", "2")
    assert memory.list_memory() == {"a": "1", "b": "2"}


def test_legacy_plain_string_values_still_read(tmp_path, monkeypatch):
    f = tmp_path / "memory.json"
    f.write_text('{"old_key": "plain string"}', encoding="utf-8")
    monkeypatch.setattr(memory, "MEMORY_FILE", f)
    assert memory.recall("old_key") == "plain string"
    assert memory.list_memory() == {"old_key": "plain string"}


def test_recent_sorts_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("first", "1")
    memory.remember("second", "2")  # newer
    keys = [k for k, _ in memory.recent(limit=10)]
    assert keys == ["second", "first"]


def test_recent_legacy_entries_sort_oldest(tmp_path, monkeypatch):
    f = tmp_path / "memory.json"
    f.write_text('{"legacy": "old"}', encoding="utf-8")
    monkeypatch.setattr(memory, "MEMORY_FILE", f)
    memory.remember("modern", "new")
    keys = [k for k, _ in memory.recent(limit=10)]
    assert keys == ["modern", "legacy"]


def test_recent_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    for i in range(5):
        memory.remember(f"k{i}", str(i))
    assert len(memory.recent(limit=2)) == 2
    assert memory.recent(limit=2)[0][0] == "k4"


def test_forget_removes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory.json")
    memory.remember("temp", "x")
    assert memory.forget("temp") == "Forgot 'temp'."
    assert memory.recall("temp") == "Nothing stored under 'temp'."
