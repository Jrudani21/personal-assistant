"""Tests for DeepSeek API key resolution (assistant/deepseek_key.py).

Single source of truth: the DEEPSEEK_API_KEY env var. File-based fallbacks
(repo dir, home dir, hardcoded absolute path, KEN_DEEPSEEK_KEY_FILE) were
removed 2026-09-14 as part of a credential clean-sweep consolidating every
key to one canonical location.
"""
import pytest

from assistant import deepseek_key as dk


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Clear the env so a real key on this machine can't make a test pass
    by accident."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_no_key_returns_empty(isolate):
    assert dk.find_key() == ""
    assert dk.key_source() == "none"


def test_env_var_is_found(isolate, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert dk.find_key() == "from-env"
    assert dk.key_source() == "env:DEEPSEEK_API_KEY"


def test_key_is_stripped(isolate, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "  sk-padded  \n")
    assert dk.find_key() == "sk-padded"


def test_blank_env_is_treated_as_absent(isolate, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
    assert dk.find_key() == ""
    assert dk.key_source() == "none"


def test_find_key_and_key_source_agree(isolate, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert dk.find_key() and dk.key_source() == "env:DEEPSEEK_API_KEY"


def test_both_report_none_together(isolate):
    assert dk.find_key() == "" and dk.key_source() == "none"
