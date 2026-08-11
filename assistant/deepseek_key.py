# assistant/deepseek_key.py
"""Locate the DeepSeek API key from multiple sources."""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ABSOLUTE_FALLBACK = Path(r"C:/Users/Janak's PC/.deepseek_key")


def _read_key_file(path) -> str:
    """Read a key file, returning '' if missing/unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def find_key() -> str:
    """Return the DeepSeek API key, or '' if not found."""
    # 1. Environment variable
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key

    # 2. File named by env var
    env_file = os.environ.get("KEN_DEEPSEEK_KEY_FILE", "").strip()
    if env_file:
        key = _read_key_file(env_file)
        if key:
            return key

    # 3. Repo data dir
    key = _read_key_file(_REPO_ROOT / "data" / ".deepseek_key")
    if key:
        return key

    # 4. Home dir
    key = _read_key_file(Path.home() / ".deepseek_key")
    if key:
        return key

    # 5. Absolute fallback
    key = _read_key_file(_ABSOLUTE_FALLBACK)
    if key:
        return key

    return ""


def key_source() -> str:
    """Return a short label of which source provided the key."""
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return "env:DEEPSEEK_API_KEY"
    env_file = os.environ.get("KEN_DEEPSEEK_KEY_FILE", "").strip()
    if env_file and _read_key_file(env_file):
        return f"file:{env_file}"
    if _read_key_file(_REPO_ROOT / "data" / ".deepseek_key"):
        return "repo:data/.deepseek_key"
    if _read_key_file(Path.home() / ".deepseek_key"):
        return "home:~/.deepseek_key"
    if _read_key_file(_ABSOLUTE_FALLBACK):
        return "absolute:C:/Users/Janak's PC/.deepseek_key"
    return "none"
