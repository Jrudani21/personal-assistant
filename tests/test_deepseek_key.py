"""Tests for DeepSeek API key resolution (assistant/deepseek_key.py).

The point of this module is that KEN still finds its key when started by a
scheduled task at boot, where the user profile may not be loaded. These tests
pin that behaviour — especially that a raising Path.home() cannot take the
whole lookup down with it.
"""
import pathlib

import pytest

from assistant import deepseek_key as dk


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Point every filesystem candidate at an empty tmp dir and clear the env,
    so a real key on this machine can't make a test pass by accident."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("KEN_DEEPSEEK_KEY_FILE", raising=False)
    monkeypatch.setattr(dk, "_REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(dk, "_ABSOLUTE_FALLBACK", tmp_path / "abs" / ".deepseek_key")
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path / "home"))
    return tmp_path


def _write(path, content="secret-key"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------- nothing available ----------

def test_no_key_anywhere_returns_empty(isolate):
    assert dk.find_key() == ""
    assert dk.key_source() == "none"


# ---------- each source ----------

def test_env_var_wins(isolate, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert dk.find_key() == "from-env"
    assert dk.key_source() == "env:DEEPSEEK_API_KEY"


def test_key_file_env_var(isolate, monkeypatch):
    p = isolate / "custom" / "key.txt"
    _write(p, "from-file")
    monkeypatch.setenv("KEN_DEEPSEEK_KEY_FILE", str(p))
    assert dk.find_key() == "from-file"
    assert dk.key_source().startswith("file:")


def test_repo_data_dir(isolate):
    _write(isolate / "repo" / "data" / ".deepseek_key", "from-repo")
    assert dk.find_key() == "from-repo"
    assert dk.key_source() == "repo:data/.deepseek_key"


def test_home_dir(isolate):
    _write(isolate / "home" / ".deepseek_key", "from-home")
    assert dk.find_key() == "from-home"
    assert dk.key_source() == "home:~/.deepseek_key"


def test_absolute_fallback(isolate):
    _write(isolate / "abs" / ".deepseek_key", "from-abs")
    assert dk.find_key() == "from-abs"
    assert dk.key_source().startswith("absolute:")


# ---------- ordering ----------

def test_env_beats_every_file(isolate, monkeypatch):
    _write(isolate / "repo" / "data" / ".deepseek_key", "from-repo")
    _write(isolate / "home" / ".deepseek_key", "from-home")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert dk.find_key() == "from-env"


def test_repo_beats_home_and_absolute(isolate):
    _write(isolate / "repo" / "data" / ".deepseek_key", "from-repo")
    _write(isolate / "home" / ".deepseek_key", "from-home")
    _write(isolate / "abs" / ".deepseek_key", "from-abs")
    assert dk.find_key() == "from-repo"


def test_home_beats_absolute(isolate):
    _write(isolate / "home" / ".deepseek_key", "from-home")
    _write(isolate / "abs" / ".deepseek_key", "from-abs")
    assert dk.find_key() == "from-home"


# ---------- the boot case this module exists for ----------

def test_survives_path_home_raising(isolate, monkeypatch):
    """A scheduled task starting at boot may have no resolvable home dir.
    Path.home() raises RuntimeError there; if that isn't guarded the whole
    lookup dies and KEN refuses to start with no obvious cause."""
    def boom():
        raise RuntimeError("no home directory")
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(boom))
    _write(isolate / "abs" / ".deepseek_key", "from-abs")
    assert dk.find_key() == "from-abs"
    assert dk.key_source().startswith("absolute:")


def test_unreadable_file_falls_through(isolate, monkeypatch):
    monkeypatch.setenv("KEN_DEEPSEEK_KEY_FILE", str(isolate / "does-not-exist"))
    _write(isolate / "home" / ".deepseek_key", "from-home")
    assert dk.find_key() == "from-home"


def test_blank_file_is_skipped(isolate):
    _write(isolate / "repo" / "data" / ".deepseek_key", "   \n  ")
    _write(isolate / "home" / ".deepseek_key", "from-home")
    assert dk.find_key() == "from-home"


def test_key_is_stripped(isolate):
    _write(isolate / "repo" / "data" / ".deepseek_key", "  sk-padded  \n")
    assert dk.find_key() == "sk-padded"


# ---------- the two functions must never disagree ----------

def test_find_key_and_key_source_agree(isolate):
    _write(isolate / "home" / ".deepseek_key", "from-home")
    assert dk.find_key() and dk.key_source() == "home:~/.deepseek_key"


def test_both_report_none_together(isolate):
    assert dk.find_key() == "" and dk.key_source() == "none"
