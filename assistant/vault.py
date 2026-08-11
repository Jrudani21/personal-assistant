"""Read the Obsidian brain vault and build a compact "gist" context pack.

The vault (default ~/brain, override with the OBSIDIAN_VAULT env var or the
vault_dir setting) is a git-backed markdown note collection. This module is
UI-facing and dependency-free: no ollama, no embeddings — it reads files and
git only, so it works even when Ollama is down.

Used by pages/2_Brain_and_Memory.py to power the vault explorer and the
"gist" generator (memory + brain -> a short context pack you can paste into
any AI chat so it gets the gist in one shot).
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
from pathlib import Path

DEFAULT_VAULT = Path(os.environ.get("OBSIDIAN_VAULT", str(Path.home() / "brain")))
SKIP_DIRS = {".obsidian", ".trash", ".git", "templates"}

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_ACTIVE_RE = re.compile(r"^status:\s*active\b", re.MULTILINE)


def vault_dir() -> Path:
    """Resolve the vault path: config.json vault_dir -> OBSIDIAN_VAULT env -> ~/brain."""
    from . import config as _config

    cfg = _config.get("vault_dir")
    if cfg:
        return Path(cfg)
    return DEFAULT_VAULT


def list_notes() -> list[dict]:
    """Every .md note in the vault, sorted by path, with light metadata.

    Each entry: {rel, folder, name, mtime, chars, lines, heading, snippet, links}
    """
    vdir = vault_dir()
    if not vdir.exists():
        return []
    notes = []
    for p in sorted(vdir.rglob("*.md")):
        rel = p.relative_to(vdir)
        if SKIP_DIRS & set(rel.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        notes.append({
            "rel": rel.as_posix(),
            "folder": rel.parent.as_posix() if rel.parent.as_posix() != "." else "",
            "name": p.stem,
            "mtime": datetime.datetime.fromtimestamp(p.stat().st_mtime),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "heading": _first_heading(text),
            "snippet": _snippet(text),
            "links": _wikilinks(text),
        })
    return notes


def read_note(rel: str) -> str | None:
    """Read a note by its vault-relative path. Path-traversal safe."""
    vdir = vault_dir().resolve()
    p = (vdir / rel).resolve()
    if not str(p).startswith(str(vdir)) or not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def search_notes(query: str, notes: list[dict] | None = None) -> list[dict]:
    """Case-insensitive full-text search over note bodies, with match snippets."""
    q = (query or "").strip().lower()
    if not q:
        return []
    notes = notes if notes is not None else list_notes()
    hits = []
    for n in notes:
        text = (read_note(n["rel"]) or "").lower()
        idx = text.find(q)
        if idx == -1:
            continue
        start = max(0, idx - 120)
        end = min(len(text), idx + len(q) + 160)
        hits.append({
            **n,
            "match_snippet": (read_note(n["rel"]) or "")[start:end].replace("\n", " "),
        })
    return hits


def recent_notes(n: int = 5) -> list[dict]:
    """Newest notes by file mtime."""
    return sorted(list_notes(), key=lambda x: x["mtime"], reverse=True)[:n]


def git_info() -> dict:
    """Best-effort git state for the vault repo (it's git-backed)."""
    vdir = vault_dir()
    info = {"ok": False, "branch": None, "dirty": None, "last_commit": None, "error": None}
    if not (vdir / ".git").exists():
        info["error"] = "not a git repo"
        return info

    def _run(args: list[str]) -> str | None:
        try:
            r = subprocess.run(
                ["git", "-C", str(vdir), *args],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = _run(["status", "--porcelain"])
    last = _run(["log", "-1", "--format=%h %ad %s", "--date=short"])
    if branch is None and dirty is None and last is None:
        info["error"] = "git unavailable"
        return info
    info.update(
        ok=True,
        branch=branch,
        dirty=len(dirty.splitlines()) if dirty else 0,
        last_commit=last,
    )
    return info


def git_status_text() -> str:
    """Full `git status --short` output for the vault, or an error string."""
    vdir = vault_dir()
    try:
        r = subprocess.run(
            ["git", "-C", str(vdir), "status", "--short"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "(clean)"
    except Exception as e:
        return f"(git unavailable: {e})"


def build_gist(memory_limit: int = 12, snippet_chars: int = 280,
               include_git: bool = True) -> str:
    """Assemble the context pack: memory facts + brain home / MOCs / active
    projects / conventions / recent notes / inbox (+ vault git state).

    Markdown, bounded in size, ready to paste into any AI chat.
    """
    from . import memory as _memory

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"# Context pack — {now}",
        "",
        "> Auto-generated from the assistant's memory + the Obsidian brain vault. "
        "This is the gist of who the user is, what they're working on, and what's "
        "in their notes.",
        "",
    ]

    # --- memory ---------------------------------------------------------
    entries = _memory.recent_entries(memory_limit)
    if entries:
        parts.append("## User (memory)")
        for e in entries:
            line = f"- **{e['key']}**: {e['value']}"
            if e["facts"]:
                line += " — " + "; ".join(e["facts"])
            if e["concepts"]:
                line += f"  _({', '.join(e['concepts'])})_"
            parts.append(line)
        parts.append("")

    notes = list_notes()
    if not notes:
        parts.append("_Vault empty or not found._")
        return "\n".join(parts)

    used_names = set()
    by_name = {n["name"]: n for n in notes}

    # --- home ------------------------------------------------------------
    home = by_name.get("_home")
    if home:
        text = (read_note(home["rel"]) or "").strip()
        parts.append("## Brain home")
        parts.append(text[: snippet_chars * 2])
        parts.append("")
        used_names.add("_home")

    # --- maps of content --------------------------------------------------
    mocs = [n for n in notes if n["name"].endswith("MOC")]
    if mocs:
        parts.append("## Maps of content")
        for m in mocs:
            text = read_note(m["rel"]) or ""
            parts.append(f"### {m['name']}")
            parts.append(_snippet(text, snippet_chars))
            used_names.add(m["name"])
        parts.append("")

    # --- active projects ---------------------------------------------------
    active = []
    for n in notes:
        if n["folder"] == "projects":
            text = read_note(n["rel"]) or ""
            if _ACTIVE_RE.search(text):
                active.append((n, text))
    if active:
        parts.append("## Active projects")
        for n, text in active:
            parts.append(f"### {n['name']}")
            parts.append(_snippet(text, snippet_chars))
            used_names.add(n["name"])
        parts.append("")

    # --- vault conventions --------------------------------------------------
    conv = by_name.get("Conventions")
    if conv:
        parts.append("## Vault conventions (how to work in this vault)")
        parts.append(_snippet(read_note(conv["rel"]) or "", snippet_chars))
        parts.append("")
        used_names.add("Conventions")

    # --- recently touched notes ---------------------------------------------
    recent = [
        n for n in sorted(notes, key=lambda x: x["mtime"], reverse=True)
        if n["name"] not in used_names
        and n["folder"] not in ("inbox", "templates")
    ][:5]
    if recent:
        parts.append("## Recently touched notes")
        for n in recent:
            parts.append(f"- **{n['name']}** ({n['mtime']:%Y-%m-%d}) — {n['snippet'][:snippet_chars]}")
        parts.append("")

    # --- inbox ---------------------------------------------------------------
    inbox = [n for n in notes if n["folder"] == "inbox"]
    if inbox:
        parts.append("## Inbox (unfiled)")
        for n in inbox:
            parts.append(f"- **{n['name']}** — {n['snippet'][:snippet_chars]}")
        parts.append("")

    # --- git state ------------------------------------------------------------
    if include_git:
        gi = git_info()
        if gi["ok"]:
            dirty = f", {gi['dirty']} uncommitted change{'s' if gi['dirty'] != 1 else ''}" if gi["dirty"] else ""
            parts.append("## Vault git")
            parts.append(f"Branch `{gi['branch']}`{dirty} · last commit: {gi['last_commit']}")
            parts.append("")

    return "\n".join(parts)


# ---- small text helpers ---------------------------------------------------
def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _first_heading(text: str) -> str:
    for line in _strip_frontmatter(text).splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def _snippet(text: str, limit: int = 300) -> str:
    body = _strip_frontmatter(text)
    lines = [ln.strip() for ln in body.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    return " ".join(lines)[:limit]


def _wikilinks(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK_RE.finditer(text):
        # Collapse internal whitespace, not just the ends: a [[link]] that wraps
        # across a line break captures the newline, and "Foo\nbar" would never
        # match the note named "Foo bar" — which silently turns a real link into
        # a phantom unresolved node in the graph. Obsidian normalizes the same way.
        target = " ".join(m.group(1).split())
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out
