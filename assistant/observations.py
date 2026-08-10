"""Append-only JSONL log of tool calls — observation capture.

Every tool invocation the assistant makes is recorded with a timestamp, the
tool name, and truncated copies of its arguments and result. This is the raw
material for distilling durable facts later (claude-mem's observation-capture
idea — see brain/notes/Memory system design study.md): memory can be built
from what the assistant actually does, not just from what it happens to be
prompted to remember.

Design notes:
- Append-only JSONL (nanobot's history.jsonl pattern): O(1) writes, crash-safe.
- Truncated args/results keep the log bounded; each line records whether the
  truncation happened so nothing is silently hidden.
- The file is capped by size (cheap stat check per append) rather than a line
  count, so the log never grows without bound.
- Logging failures are swallowed — observation capture must never break a chat.
"""
import datetime
import json
from pathlib import Path

OBSERVATIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "observations.jsonl"
MAX_FILE_BYTES = 2 * 1024 * 1024  # compact when the log exceeds ~2 MB
KEEP_ENTRIES = 2000  # how many most-recent entries survive a compaction
ARG_CHARS = 400
RESULT_CHARS = 800


def _truncate(text, limit: int) -> tuple[str, bool]:
    text = str(text)
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"…[{len(text) - limit} more chars]", True


def append(name: str, args, result) -> None:
    """Record one tool call. Best-effort: never raises."""
    try:
        args_str = json.dumps(args, ensure_ascii=False, default=str)
        result_str = str(result)
        args_short, args_truncated = _truncate(args_str, ARG_CHARS)
        result_short, result_truncated = _truncate(result_str, RESULT_CHARS)
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "tool": name,
            "args": args_short,
            "args_truncated": args_truncated,
            "result": result_short,
            "result_truncated": result_truncated,
        }
        with open(OBSERVATIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _maybe_compact()
    except Exception:
        pass  # observation capture must never break the chat


def _maybe_compact() -> None:
    try:
        if OBSERVATIONS_FILE.stat().st_size <= MAX_FILE_BYTES:
            return
        lines = OBSERVATIONS_FILE.read_text(encoding="utf-8").splitlines()
        kept = lines[-KEEP_ENTRIES:]
        tmp = OBSERVATIONS_FILE.with_suffix(OBSERVATIONS_FILE.suffix + ".tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(OBSERVATIONS_FILE)
    except Exception:
        pass


def read(limit: int = 50) -> list[dict]:
    """The most recent `limit` observations, oldest-of-the-batch first."""
    if not OBSERVATIONS_FILE.exists():
        return []
    entries = []
    try:
        with open(OBSERVATIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return entries[-limit:]


def count() -> int:
    if not OBSERVATIONS_FILE.exists():
        return 0
    try:
        return sum(1 for line in OBSERVATIONS_FILE.open(encoding="utf-8") if line.strip())
    except Exception:
        return 0


def clear() -> str:
    try:
        if OBSERVATIONS_FILE.exists():
            OBSERVATIONS_FILE.unlink()
        return "Cleared the tool-activity log."
    except Exception as e:
        return f"Error clearing log: {e}"
