"""Non-owner sessions must not reach the owner's private data. Offline: the
token store, chats dir and providers are all faked; nothing touches data/."""
import asyncio
import queue
from types import SimpleNamespace

import pytest

server = pytest.importorskip("server", reason="server.py needs fastapi/openai installed")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import llm, sessions, tools  # noqa: E402

OWNER = "owner-token-fixture"
FACT = {"key": "secret_pet", "value": "Rex the dog", "facts": [], "concepts": []}
REMINDER = {"id": 7, "text": "dentist appointment", "due_at": "2026-01-01T09:00"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "TOKENS_FILE", tmp_path / ".ken_tokens.json")
    monkeypatch.setattr(server, "TOKEN_FILE", tmp_path / ".ken_token")
    monkeypatch.setattr(server, "USERS_FILE", tmp_path / ".ken_users.json")
    monkeypatch.setattr(server, "AUTH_LOG", tmp_path / "access.log")
    monkeypatch.setattr(server, "ACCESS_TOKEN", OWNER)
    monkeypatch.setattr(server, "AUTH_ENABLED", True)
    chats = tmp_path / "chats"
    chats.mkdir()
    monkeypatch.setattr(sessions, "CHATS_DIR", chats)
    # Owner-private stores, stubbed so nothing real is read.
    fired = []
    monkeypatch.setattr(llm.memory, "recent_entries", lambda *a, **k: [FACT])
    monkeypatch.setattr(llm.memory, "list_memory", lambda *a, **k: [FACT])
    monkeypatch.setattr(llm.reminders, "due_reminders", lambda *a, **k: [REMINDER])
    monkeypatch.setattr(llm.reminders, "mark_fired", lambda rid: fired.append(rid))
    monkeypatch.setattr(server.observations, "append", lambda *a, **k: None)
    monkeypatch.setattr(server, "_CHAT_OWNERS", {}, raising=False)
    guest = server.create_guest_token("sam", hours=1)["token"]
    return SimpleNamespace(tmp=tmp_path, guest=guest, fired=fired)


def _client():
    return TestClient(server.app)  # no `with`: skips the startup key check


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


BLOCKED = [
    ("GET", "/api/chats"), ("GET", "/api/chats/search?q=x"),
    ("GET", "/api/chats/0123456789ab"), ("DELETE", "/api/chats/0123456789ab"),
    ("GET", "/api/chats/0123456789ab/export"),
    ("GET", "/api/memory"), ("POST", "/api/memory"), ("POST", "/api/deep-analysis"),
    ("GET", "/api/vault/notes"), ("GET", "/api/vault/note?rel=a.md"),
    ("GET", "/api/vault/search?q=x"), ("GET", "/api/vault/git"),
    ("POST", "/api/vault/gist"), ("DELETE", "/api/vault/gist"), ("POST", "/api/vault/sync"),
    ("GET", "/api/vault/graph"), ("GET", "/api/vault/graphiti?q=x"),
    ("GET", "/api/documents"), ("POST", "/api/documents"), ("DELETE", "/api/documents/a.txt"),
    ("GET", "/api/backups"), ("POST", "/api/backups"),
    ("POST", "/api/knowledge/sync"),
    ("GET", "/api/users"), ("POST", "/api/users"), ("DELETE", "/api/users/bob"),
    ("GET", "/api/tasks"), ("POST", "/api/tasks"), ("PATCH", "/api/tasks/1"), ("DELETE", "/api/tasks/1"),
    ("GET", "/api/reminders"), ("POST", "/api/reminders"), ("DELETE", "/api/reminders/1"),
]


@pytest.mark.parametrize("method,path", BLOCKED)
def test_guest_gets_403_on_owner_private_routes(env, method, path):
    # Checked first so an unblocked route never reaches its real handler.
    assert server._guest_blocked(path.split("?")[0])
    r = _client().request(method, path, headers=_hdr(env.guest))
    assert r.status_code == 403, (method, path, r.status_code)


@pytest.mark.parametrize("method,path", [("GET", "/api/memory"), ("GET", "/api/chats"),
                                         ("GET", "/api/tasks"), ("GET", "/api/vault/notes")])
def test_password_user_is_also_isolated(env, method, path):
    assert server._guest_blocked(path)
    assert server.create_user("friend", "secret-pw")
    c = _client()
    c.cookies.set(server.SESSION_COOKIE, server._new_session("friend"))
    assert c.request(method, path).status_code == 403


def test_owner_still_reaches_private_routes(env, monkeypatch):
    monkeypatch.setattr(server.sessions, "list_chats", lambda: [])
    monkeypatch.setattr(server.memory, "recent_entries", lambda *a, **k: [FACT])
    monkeypatch.setattr(server.todo, "get_tasks", lambda: [])
    monkeypatch.setattr(server.reminders, "get_reminders", lambda: [])
    monkeypatch.setattr(server.reminders, "due_reminders", lambda: [])
    monkeypatch.setattr(server.rag, "list_documents", lambda: [])
    c = _client()
    for path in ("/api/chats", "/api/memory", "/api/tasks", "/api/reminders",
                 "/api/documents", "/api/users"):
        assert c.get(path, headers=_hdr(OWNER)).status_code == 200, path


@pytest.mark.parametrize("path", ["/api/tools", "/api/whoami"])
def test_guest_keeps_basic_routes(env, path):
    assert _client().get(path, headers=_hdr(env.guest)).status_code == 200


PRIVATE_TOOLS = [
    "recall", "remember", "distill_memory", "search_documents", "sync_knowledge",
    "sync_vault", "list_tasks", "add_task", "complete_task", "cancel_reminder",
    "list_reminders", "remind_me", "recent_activity", "backup_data", "mcp_list_tools",
    "deep_analysis",
]


@pytest.mark.parametrize("tool", PRIVATE_TOOLS)
def test_private_data_tools_are_guest_blocked(tool):
    assert tool in tools.REGISTRY
    assert tool in server.GUEST_BLOCKED_TOOLS


def test_guest_tool_list_hides_private_tools(env):
    names = {t["name"] for t in _client().get("/api/tools", headers=_hdr(env.guest)).json()}
    assert not names & set(PRIVATE_TOOLS)
    assert "fetch_webpage" in names


# ---------- chat turn plumbing ----------

class FakeClient:
    """OpenAI-style client that streams one chunk and records each request."""

    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        delta = SimpleNamespace(content="ok", tool_calls=None)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)])])

    @property
    def system(self):
        return self.requests[-1]["messages"][0]["content"]


@pytest.fixture
def providers(env, monkeypatch):
    fake = FakeClient()
    hermes_calls = []

    def fake_hermes(history, chat_id):
        hermes_calls.append(chat_id)
        yield "Error talking to Hermes: offline"

    monkeypatch.setattr(server, "_deepseek_client", fake)
    monkeypatch.setattr(server, "HERMES_A2A_ENABLED", True)
    monkeypatch.setattr(server, "_hermes_stream", fake_hermes)
    for name in ("_bluesminds_client", "_groq_client", "_openrouter_client"):
        monkeypatch.setattr(server, name, None)
    monkeypatch.setattr(server, "FALLBACK_MODEL", "")
    return SimpleNamespace(client=fake, hermes=hermes_calls)


def _run_worker(guest: bool):
    q = queue.Queue()
    chat = sessions.new_chat()
    history = [{"role": "user", "content": "what do you know about me?"}]
    server._chat_worker(server.MODEL, history, set(), chat, q, guest=guest)
    events = []
    while not q.empty():
        events.append(q.get())
    assert events[-1][0] == "done", events
    return events


def test_guest_prompt_has_no_memory_or_reminders(env, providers):
    _run_worker(guest=True)
    system = providers.client.system
    assert "Rex the dog" not in system
    assert "dentist" not in system
    assert env.fired == []
    assert providers.hermes == []


def test_owner_prompt_unchanged(env, providers):
    _run_worker(guest=False)
    system = providers.client.system
    assert "Rex the dog" in system
    assert "dentist" in system
    assert env.fired == [7]
    assert len(providers.hermes) == 1


def test_owner_default_system_prompt_is_unchanged(env):
    assert llm._system_prompt() == llm._system_prompt(guest=False)
    assert "Rex the dog" not in llm._system_prompt(guest=True)


def test_fallback_providers_get_guest_prompt(env, monkeypatch):
    fake = FakeClient()
    list(server._openai_compat_stream_chat(fake, "m", [{"role": "user", "content": "hi"}],
                                           "Groq", guest=True))
    assert "Rex the dog" not in fake.system

    seen = []

    def fake_stream(model, messages, tools=None):
        seen.append(messages[0]["content"])
        return iter([{"message": {"content": "ok"}}])

    monkeypatch.setattr(llm._local, "chat_stream", fake_stream)
    list(llm.stream_chat("m", [{"role": "user", "content": "hi"}], guest=True))
    assert "Rex the dog" not in seen[-1] and "dentist" not in seen[-1]
    assert env.fired == []


async def _collect(gen):
    return [ev async for ev in gen]


def _chat(chat_id, role, who, tools_=()):
    req = server.ChatRequest(messages=[{"role": "user", "content": "hello"}],
                             enabled_tools=list(tools_), chat_id=chat_id)
    events = asyncio.run(_collect(server._sse_chat(req, role, who)))
    import json
    done = [json.loads(e["data"]) for e in events if e["event"] == "done"]
    assert done, events
    return done[0]["chat_id"]


def test_guest_cannot_continue_owner_chat(env, providers):
    owner_chat = {"id": "0123456789ab", "title": "Owner secrets",
                  "messages": [{"role": "user", "content": "my bank pin is 1234"}]}
    sessions.save(owner_chat)
    before = (sessions.CHATS_DIR / "0123456789ab.jsonl").read_text(encoding="utf-8")

    got = _chat("0123456789ab", "guest", "guest:abc")
    assert got != "0123456789ab"
    assert (sessions.CHATS_DIR / "0123456789ab.jsonl").read_text(encoding="utf-8") == before
    # A guest's own chat id keeps working for that guest only.
    assert _chat(got, "guest", "guest:abc") == got
    assert _chat(got, "guest", "guest:other") != got


def test_owner_can_continue_own_chat(env, providers):
    sessions.save({"id": "0123456789ab", "title": "t",
                   "messages": [{"role": "user", "content": "x"}]})
    assert _chat("0123456789ab", "owner", "owner") == "0123456789ab"


def test_guest_chat_strips_private_tools(env, providers):
    _chat(None, "guest", "guest:abc", tools_=["recall", "calculator", "list_reminders"])
    offered = {s["function"]["name"] for s in providers.client.requests[-1].get("tools") or []}
    assert offered == {"calculator"}
