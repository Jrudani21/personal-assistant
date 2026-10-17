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
    "deep_analysis", "list_skills",
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


# ---------- deep-analysis cache ----------

def test_crew_cache_skipped_when_disabled(monkeypatch):
    crew = pytest.importorskip("assistant.crew")
    calls = []
    monkeypatch.setattr(crew.crew_cache, "get_cached", lambda *a, **k: calls.append("get") or "OWNER REPORT")
    monkeypatch.setattr(crew.crew_cache, "store", lambda *a, **k: calls.append("store"))
    monkeypatch.setattr(crew, "build_crew", lambda *a, **k: SimpleNamespace(kickoff=lambda: "fresh"))
    monkeypatch.setattr(crew, "_used_local_fallback", lambda c: False)
    assert crew.run_deep_analysis("q", use_cache=False) == "fresh"
    assert calls == []
    # Default (owner) path still uses the cache.
    assert crew.run_deep_analysis("q").startswith("OWNER REPORT")


def test_non_owner_deep_analysis_bypasses_cache(env, monkeypatch):
    crew = pytest.importorskip("assistant.crew")
    import json
    monkeypatch.setattr(server.crew_cache, "get_cached", lambda *a, **k: "OWNER REPORT")
    seen = {}
    monkeypatch.setattr(crew, "run_deep_analysis",
                        lambda q, **kw: seen.update(kw) or "fresh")
    req = server.DeepAnalysisRequest(question="same question")
    events = asyncio.run(_collect(server._sse_deep_analysis(req, owner=False)))
    done = [json.loads(e["data"]) for e in events if e["event"] == "done"]
    assert done[0]["findings"] == "fresh" and done[0]["cached"] is False
    assert seen == {"use_cache": False}


def test_guest_deep_analysis_route_never_touches_cache(env, monkeypatch):
    touched = []
    monkeypatch.setattr(server.crew_cache, "get_cached", lambda *a, **k: touched.append(1))
    r = _client().post("/api/deep-analysis", json={"question": "x"}, headers=_hdr(env.guest))
    assert r.status_code == 403 and touched == []


# ---------- activity log ----------

class ToolCallClient(FakeClient):
    """First request asks for one calculator call, later requests answer."""

    def _create(self, **kw):
        self.requests.append(kw)
        if len(self.requests) == 1:
            fn = SimpleNamespace(name="calculator", arguments='{"expression": "1+1"}')
            tc = SimpleNamespace(index=0, id="c1", function=fn)
            delta = SimpleNamespace(content=None, tool_calls=[tc])
        else:
            delta = SimpleNamespace(content="2", tool_calls=None)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)])])


@pytest.mark.parametrize("guest,expected", [(True, 0), (False, 1)])
def test_activity_log_only_records_owner_tool_calls(env, providers, monkeypatch, guest, expected):
    logged = []
    monkeypatch.setattr(server.observations, "append", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(server, "_deepseek_client", ToolCallClient())
    q = queue.Queue()
    server._chat_worker(server.MODEL, [{"role": "user", "content": "1+1?"}],
                        {"calculator"}, sessions.new_chat(), q, guest=guest)
    assert len(logged) == expected

    logged.clear()
    list(server._openai_compat_stream_chat(ToolCallClient(), "m",
                                           [{"role": "user", "content": "1+1?"}],
                                           "Groq", use_tools=True, guest=guest))
    assert len(logged) == expected


@pytest.mark.parametrize("guest,expected", [(True, 0), (False, 1)])
def test_local_loop_activity_log_only_records_owner(env, monkeypatch, guest, expected):
    logged = []
    monkeypatch.setattr(llm.observations, "append", lambda *a, **k: logged.append(a))
    call = {"function": {"name": "calculator", "arguments": {"expression": "1+1"}}}
    replies = iter([{"message": {"content": "", "tool_calls": [call]}},
                    {"message": {"content": "2"}}])
    monkeypatch.setattr(llm._local, "chat", lambda **kw: next(replies))
    llm.run_chat("m", [{"role": "user", "content": "hi"}], guest=guest)
    assert len(logged) == expected

    logged.clear()
    streams = iter([iter([{"message": {"content": "", "tool_calls": [call]}}]),
                    iter([{"message": {"content": "2"}}])])
    monkeypatch.setattr(llm._local, "chat_stream", lambda **kw: next(streams))
    list(llm.stream_chat("m", [{"role": "user", "content": "hi"}], guest=guest))
    assert len(logged) == expected


# ---------- base prompt ----------

def test_guest_prompt_ignores_custom_and_owner_name(env, monkeypatch):
    real_get = llm._config.get
    monkeypatch.setattr(llm._config, "get",
                        lambda k, d=None: "CUSTOM OWNER PROMPT" if k == "system_prompt" else real_get(k, d))
    guest = llm._system_prompt(guest=True)
    assert "CUSTOM OWNER PROMPT" not in guest
    assert "Janak" not in guest
    assert "Current date/time:" in guest
    assert llm._system_prompt().startswith("CUSTOM OWNER PROMPT")


def test_guest_default_prompt_has_no_owner_name(env):
    assert "Janak" not in llm._system_prompt(guest=True)
    assert llm._system_prompt().startswith(llm.BASE_SYSTEM_PROMPT)


# ---------- web UI ----------

OWNER_ONLY_UI_IDS = ["sbChats", "sbBrain", "sbMemory", "sbTasks", "sbDocs", "sbDeep",
                     "chatsBtn", "brainBtn", "memBtn", "tasksBtn", "docsBtn", "daBtn"]


@pytest.mark.parametrize("who", ["owner", "guest"])
def test_ui_hides_owner_panels_for_non_owners(env, who):
    import re
    tok = OWNER if who == "owner" else env.guest
    r = _client().get("/", headers=_hdr(tok))
    assert r.status_code == 200
    html = r.text
    m = re.search(r"const OWNER_ONLY_UI = \[(.*?)\];", html, re.S)
    assert m, "OWNER_ONLY_UI list missing"
    listed = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert set(OWNER_ONLY_UI_IDS) <= listed
    for scrim in ("chatsScrim", "brainScrim", "memScrim", "tasksScrim", "docsScrim", "daScrim"):
        assert f'[data-open="{scrim}"]' in m.group(1)
    # Hidden only on the non-owner branch of the whoami check.
    assert re.search(r'if \(d\.role === "owner"\) \{[^}]*\} else \{[^}]*OWNER_ONLY_UI', html)
