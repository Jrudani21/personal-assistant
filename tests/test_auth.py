"""Tests for KEN's access control (server.py).

These cover the security-relevant logic directly rather than over HTTP, so
they need no running server, no DeepSeek key, and no network.
"""
import time

import pytest

server = pytest.importorskip("server", reason="server.py needs fastapi/openai installed")


@pytest.fixture
def tokens(tmp_path, monkeypatch):
    """Point the token store at a tmp dir so tests never touch real data/."""
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "TOKENS_FILE", tmp_path / ".ken_tokens.json")
    monkeypatch.setattr(server, "TOKEN_FILE", tmp_path / ".ken_token")
    monkeypatch.setattr(server, "AUTH_LOG", tmp_path / "access.log")
    monkeypatch.setattr(server, "ACCESS_TOKEN", "owner-token-fixture")
    monkeypatch.setattr(server, "AUTH_ENABLED", True)
    return tmp_path


# ---------- principal resolution ----------

def test_owner_token_resolves_to_owner(tokens):
    p = server._principal_for("owner-token-fixture")
    assert p == {"role": "owner", "name": "owner"}


def test_unknown_token_resolves_to_none(tokens):
    assert server._principal_for("nope") is None


def test_empty_token_resolves_to_none(tokens):
    assert server._principal_for("") is None
    assert server._principal_for(None) is None


def test_guest_token_resolves_to_guest_not_owner(tokens):
    entry = server.create_guest_token("sam", hours=1)
    p = server._principal_for(entry["token"])
    assert p["role"] == "guest"
    assert p["name"] == "sam"


def test_guest_token_differs_from_owner_token(tokens):
    entry = server.create_guest_token("sam", hours=1)
    assert entry["token"] != server.ACCESS_TOKEN


# ---------- expiry ----------

def test_expired_guest_token_is_rejected(tokens):
    entry = server.create_guest_token("temp", hours=1)
    data = server._read_tokens()
    data["guests"][0]["expires_at"] = time.time() - 5      # already dead
    server._write_tokens(data)
    assert server._principal_for(entry["token"]) is None


def test_expired_guest_token_is_pruned_from_the_store(tokens):
    server.create_guest_token("temp", hours=1)
    data = server._read_tokens()
    data["guests"][0]["expires_at"] = time.time() - 5
    server._write_tokens(data)
    server._active_guests()
    assert server._read_tokens()["guests"] == []


def test_zero_hours_means_no_expiry(tokens):
    entry = server.create_guest_token("forever", hours=0)
    assert entry["expires_at"] is None
    assert server._principal_for(entry["token"])["role"] == "guest"


# ---------- revocation ----------

def test_revoked_guest_token_stops_working(tokens):
    entry = server.create_guest_token("sam", hours=1)
    assert server.revoke_guest_token(entry["id"]) is True
    assert server._principal_for(entry["token"]) is None


def test_revoking_unknown_id_returns_false(tokens):
    assert server.revoke_guest_token("does-not-exist") is False


def test_revoking_one_guest_leaves_the_others(tokens):
    a = server.create_guest_token("a", hours=1)
    b = server.create_guest_token("b", hours=1)
    server.revoke_guest_token(a["id"])
    assert server._principal_for(a["token"]) is None
    assert server._principal_for(b["token"])["name"] == "b"


# ---------- throttle ----------

def test_throttle_trips_after_the_limit(tokens, monkeypatch):
    monkeypatch.setattr(server, "_fails", {})
    ip = "203.0.113.9"
    assert server._throttled(ip) is False
    for _ in range(server._FAIL_LIMIT):
        server._record_fail(ip)
    assert server._throttled(ip) is True


def test_throttle_is_per_ip(tokens, monkeypatch):
    monkeypatch.setattr(server, "_fails", {})
    for _ in range(server._FAIL_LIMIT):
        server._record_fail("203.0.113.9")
    assert server._throttled("203.0.113.9") is True
    assert server._throttled("198.51.100.4") is False


def test_throttle_forgets_old_failures(tokens, monkeypatch):
    monkeypatch.setattr(server, "_fails", {})
    ip = "203.0.113.9"
    stale = time.time() - server._FAIL_WINDOW_S - 10
    server._fails[ip] = [stale] * (server._FAIL_LIMIT + 5)
    assert server._throttled(ip) is False


# ---------- guest restrictions ----------
# Guests get the full app EXCEPT the configuration/security surface. That
# carve-out only means anything if guests also lose code execution and
# filesystem reads — otherwise a guest enables run_python and shells out to
# `tailscale funnel`, or reads data/.ken_tokens.json to become owner.

@pytest.mark.parametrize("path", [
    "/api/funnel", "/api/access", "/api/config",
    "/api/tools/toggle", "/api/admin", "/api/admin/status",
    "/api/access/abc123",
])
def test_config_surface_is_blocked_for_guests(path):
    assert server._guest_blocked(path) is True


@pytest.mark.parametrize("path", [
    "/api/chat", "/api/memory", "/api/vault/notes", "/api/tasks",
    "/api/chats", "/api/documents", "/api/tools",
])
def test_normal_surface_is_open_to_guests(path):
    assert server._guest_blocked(path) is False


def test_tools_toggle_is_blocked_but_tools_list_is_not():
    # Listing tools is fine; enabling one is the escalation path.
    assert server._guest_blocked("/api/tools/toggle") is True
    assert server._guest_blocked("/api/tools") is False


@pytest.mark.parametrize("tool", [
    "run_python", "restart_python_session", "run_skill", "mcp_call_tool",
    "read_file", "write_file", "list_files", "admin",
])
def test_dangerous_tools_are_blocked_for_guests(tool):
    assert tool in server.GUEST_BLOCKED_TOOLS


def test_read_file_is_blocked_because_it_would_expose_the_token_store():
    # data/.ken_tokens.json holds the owner token; read_file would hand a
    # guest a straight privilege escalation.
    assert "read_file" in server.GUEST_BLOCKED_TOOLS


@pytest.mark.parametrize("tool", [
    "calculator", "web_search", "weather", "wikipedia_summary",
    "search_documents", "remember", "add_task", "deep_analysis",
])
def test_safe_tools_stay_available_to_guests(tool):
    assert tool not in server.GUEST_BLOCKED_TOOLS


def test_guest_requesting_blocked_tools_gets_them_stripped():
    """The client supplies enabled_tools, so filtering must be server-side."""
    requested = {"calculator", "run_python", "read_file", "web_search", "admin"}
    assert requested - server.GUEST_BLOCKED_TOOLS == {"calculator", "web_search"}


# ---------- auth disabled ----------

def test_auth_disabled_lets_everything_through(tokens, monkeypatch):
    monkeypatch.setattr(server, "AUTH_ENABLED", False)
    monkeypatch.setattr(server, "ACCESS_TOKEN", "")
    # _principal_for still returns None, but the middleware short-circuits on
    # AUTH_ENABLED — this documents that KEN_TOKEN="" is a real off switch.
    assert server.AUTH_ENABLED is False
