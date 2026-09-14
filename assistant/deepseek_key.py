# assistant/deepseek_key.py
"""Locate the DeepSeek API key from the environment variable DEEPSEEK_API_KEY."""

import os


def _candidates():
    """Yield (label, key) pairs in resolution order, lazily."""
    # Environment variable — the sole source
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        yield ("env:DEEPSEEK_API_KEY", env_key)


def find_key() -> str:
    """Return the DeepSeek API key, or '' if not found."""
    for _label, key in _candidates():
        return key
    return ""


def key_source() -> str:
    """Return a short label of which source provided the key."""
    for label, _key in _candidates():
        return label
    return "none"


if __name__ == "__main__":
    source = key_source()
    found = bool(find_key())
    print(f"key_source: {source}")
    print(f"key_found: {found}")
