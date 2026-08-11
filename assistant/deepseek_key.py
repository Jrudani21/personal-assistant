# assistant/deepseek_key.py
"""Locate the DeepSeek API key from multiple sources."""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ABSOLUTE_FALLBACK = Path(r"C:/Users/Janak's PC/.deepseek_key")


def _read_key_file(path) -> str:
    """Read a key file, returning '' if missing/unreadable.

    Catches Exception, not just OSError/UnicodeDecodeError: this runs during
    server import, and a bad path type or an exotic filesystem error must
    degrade to the next candidate rather than stop KEN from starting.
    """
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _candidates():
    """Yield (label, key) pairs in resolution order, lazily."""
    # 1. Environment variable
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        yield ("env:DEEPSEEK_API_KEY", env_key)

    # 2. File named by env var
    env_file = os.environ.get("KEN_DEEPSEEK_KEY_FILE", "").strip()
    if env_file:
        key = _read_key_file(env_file)
        if key:
            yield (f"file:{env_file}", key)

    # 3. Repo data dir
    key = _read_key_file(_REPO_ROOT / "data" / ".deepseek_key")
    if key:
        yield ("repo:data/.deepseek_key", key)

    # 4. Home dir — Path.home() can raise RuntimeError when no home
    #    directory is resolvable (e.g., scheduled task at boot with no
    #    user profile loaded), so guard the entire computation.
    try:
        home = Path.home()
        key = _read_key_file(home / ".deepseek_key")
        if key:
            yield ("home:~/.deepseek_key", key)
    except Exception:
        pass

    # 5. Absolute fallback — hardcoded because it's the known path on
    #    the developer's machine, used when $HOME is unavailable.
    key = _read_key_file(_ABSOLUTE_FALLBACK)
    if key:
        yield ("absolute:C:/Users/Janak's PC/.deepseek_key", key)


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
