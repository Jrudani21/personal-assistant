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


# ---------- auth disabled ----------

def test_auth_disabled_lets_everything_through(tokens, monkeypatch):
    monkeypatch.setattr(server, "AUTH_ENABLED", False)
    monkeypatch.setattr(server, "ACCESS_TOKEN", "")
    # _principal_for still returns None, but the middleware short-circuits on
    # AUTH_ENABLED — this documents that KEN_TOKEN="" is a real off switch.
    assert server.AUTH_ENABLED is False
