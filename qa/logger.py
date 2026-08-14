"""Unified structured logger for the KEN fleet (append-only JSONL).

Every daemon/bot/script appends ONE line per event to a single fleet log:
data/logs/fleet.jsonl. The EOD aggregator (log_aggregate.py) reads this plus
all other log sources into ONE bounded bundle, processed by ONE LLM request.

Design:
- JSONL (one JSON object per line) — greppable, machine-readable, stable.
- Thread-safe via a file lock-less append (single line writes on Windows are
  effectively atomic for our sizes; a threading.Lock guards in-process).
- Never raises — logging must never break a bot run.

Usage:
    from qa import logger
    logger.log("bot_runner", "run_start", bot_id="weather-sweep", due=True)
    logger.log("work_queue", "enqueued", bot_id="x", order_id=3)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
FLEET_LOG = LOG_DIR / "fleet.jsonl"

_lock = threading.Lock()
_logfile = None  # injectable for tests (isolated)


def set_logfile(path) -> None:
    """Override the log file (used by self-tests to avoid prod writes)."""
    global _logfile
    _logfile = path


def log(source: str, kind: str, **payload) -> None:
    """Append one structured event. Never raises."""
    try:
        entry = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source,
            "kind": kind,
        }
        entry.update({k: v for k, v in payload.items() if v is not None})
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            path = _logfile or FLEET_LOG
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def recent(limit: int = 200, source: str | None = None) -> list[dict]:
    """Last N events (optionally filtered by source), oldest-first."""
    try:
        path = _logfile or FLEET_LOG
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        out = []
        for ln in lines[-limit:]:
            try:
                e = json.loads(ln)
                if source and e.get("source") != source:
                    continue
                out.append(e)
            except Exception:
                continue
        return out
    except Exception:
        return []


def stats() -> dict:
    try:
        path = _logfile or FLEET_LOG
        if not path.exists():
            return {"events": 0, "sources": {}}
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        by_src: dict[str, int] = {}
        for ln in lines:
            try:
                s = json.loads(ln).get("source", "?")
                by_src[s] = by_src.get(s, 0) + 1
            except Exception:
                continue
        return {"events": len(lines), "sources": by_src}
    except Exception:
        return {"events": 0, "sources": {}}


if __name__ == "__main__":
    # self-test — ISOLATED (lesson: never pollute prod stores)
    import tempfile
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="logger_test_")) / "test.jsonl"
    set_logfile(tmp)
    try:
        log("test", "ping", value=1)
        log("test", "pong", value=2)
        print("recent:", recent())
        print("stats:", stats())
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        set_logfile(None)
