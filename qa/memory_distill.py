#!/usr/bin/env python
"""Nightly memory distillation pipeline (CoALA episodic -> semantic).

Reads new daily notes (episodic: E:\\Local\\brain\\daily\\*.md), extracts
durable facts with a LOCAL model (qwen3:8b via Ollama, $0), and merges them
APPEND-ONLY into memory.json (semantic tier) with provenance via
assistant.memory.distill_merge().

Research-backed (2026-08-14): Mem0-style ADD-only extraction — store
everything append-only with timestamps + source links, resolve contradictions
at read time by recency. Cheaper and more robust than LLM-driven UPDATE/DELETE
on a small local model.

Usage:
  python qa/memory_distill.py [--dry-run] [--model qwen3:8b]
  (cron: nightly via run_clean.py wrapper — strips Hermes venv leak)

Idempotent: notes already distilled (content-hash tracked in
data/distill_state.json) are skipped. Safe to run repeatedly.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from assistant import memory  # noqa: E402

DAILY_DIR = Path(os.environ.get("BRAIN_DAILY", r"E:\Local\brain\daily"))
STATE_FILE = ROOT / "data" / "distill_state.json"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = "qwen3:8b"
MAX_NOTE_CHARS = 6000  # cap note size for extraction (context)

EXTRACT_PROMPT = """You distill durable facts from a personal daily note. \
Extract ONLY facts worth remembering long-term: user preferences, decisions, \
project state changes, key findings, addresses/identifiers, tooling choices. \
Ignore greetings, filler, ephemeral to-dos, and obvious noise.

Return STRICTLY a JSON array, no prose, no markdown fences. Each element:
{"key": "short_snake_case_group", "value": "one-line summary of the fact", \
"facts": ["specific claim 1", "specific claim 2"], "concepts": ["tag1", "tag2"]}
Use existing groups when the fact fits (e.g. "hardware", "projects", "preferences"). \
Max 8 elements. If nothing durable, return [].

Note content:
---START---
{note}
---END---"""


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _note_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ollama_chat(model: str, prompt: str, max_tokens: int = 1500) -> str:
    """Call Ollama NATIVE /api/chat (local, free).

    NOTE (2026-08-14): use /api/chat, NOT /v1/chat/completions — the /v1
    path on this box returns EMPTY content for qwen3:8b (thinking mode eats
    the budget / content lands elsewhere). /api/chat with think:false gives
    clean answers. Verified: 'Reply with exactly: OK' -> 'OK'.
    """
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("message", {}).get("content", "")


def _parse_facts(raw: str) -> list[dict]:
    """Lenient JSON-array parse from model output (strip fences/noise)."""
    if not raw:
        return []
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        # try repairing trailing commas / single quotes
        fixed = re.sub(r",\s*([}\]])", r"\1", m.group(0))
        try:
            data = json.loads(fixed)
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if isinstance(it, dict) and it.get("key") and it.get("value"):
            out.append({
                "key": str(it["key"])[:60],
                "value": str(it["value"])[:300],
                "facts": [str(f)[:300] for f in it.get("facts", [])][:5],
                "concepts": [str(c)[:40] for c in it.get("concepts", [])][:5],
            })
    return out[:8]


def distill_note(path: Path, model: str, dry_run: bool = False) -> int:
    """Distill one note into memory.json. Returns number of groups written."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return 0
    if not text.strip():
        return 0
    prompt = EXTRACT_PROMPT.replace("{note}", text[:MAX_NOTE_CHARS])
    raw = _ollama_chat(model, prompt)
    facts = _parse_facts(raw)
    written = 0
    for f in facts:
        if dry_run:
            continue
        memory.distill_merge(
            f["key"], f["value"], f["facts"], f["concepts"],
            source=f"daily/{path.name}",
        )
        written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    if not DAILY_DIR.exists():
        print(f"NO_DAILY_DIR {DAILY_DIR}")
        return 0

    state = _load_state()
    notes = sorted(DAILY_DIR.glob("*.md"))
    new_notes = []
    for p in notes:
        try:
            h = _note_hash(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if state.get(p.name) != h:
            new_notes.append((p, h))

    if not new_notes:
        print(f"NO_NEW_NOTES ({len(notes)} notes already distilled)")
        return 0

    total = 0
    for path, h in new_notes:
        try:
            n = distill_note(path, args.model, args.dry_run)
            total += n
            if not args.dry_run:
                state[path.name] = h
            print(f"DISTILLED {path.name}: {n} groups")
        except Exception as e:
            print(f"FAIL {path.name}: {str(e)[:120]}")
    if not args.dry_run:
        _save_state(state)
    print(f"TOTAL: {total} groups from {len(new_notes)} notes "
          f"({'dry-run' if args.dry_run else 'merged'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
