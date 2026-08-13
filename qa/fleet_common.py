#!/usr/bin/env python
"""Fleet common — production-grade helpers shared by all bots (big-company mode).

Implements the research-backed resiliency primitives:
  1. retry() — exponential backoff + jitter; retries ONLY transient errors
     (429/5xx/timeouts); never 4xx. 2-3 attempts.
  2. Idempotency — run_once() stamps a key in the state file so a retried
     side-effect can't fire twice.
  3. Checkpointing — checkpoint() snapshots step state to SQLite (tens of ms),
     so a crashed bot resumes from the last step, not step zero.
  4. Heartbeat — beat() updates last_heartbeat per bot; standby bots take over
     when the primary's heartbeat goes stale (fleet_boss checks this).
"""
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DB = ROOT / "data" / "fleet_state.db"
STATE_FILE = ROOT / "data" / "bots_state.json"

TRANSIENT = {429, 500, 502, 503, 504}


def _db():
    os.makedirs(STATE_DB.parent, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB), timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS checkpoints ("
                 "bot TEXT PRIMARY KEY, step TEXT, data TEXT, ts REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS idempotency ("
                 "key TEXT PRIMARY KEY, ts REAL)")
    return conn


def retry(fn, attempts: int = 3, base_delay: float = 1.0, max_delay: float = 30.0,
          on_status=None, on_exception=None):
    """Retry fn() with exponential backoff + jitter.

    Stops immediately on 4xx (permanent) or on_status returning False.
    on_status(status, body) -> bool: extra "should retry?" hook.
    Returns (ok, result, status_or_None, attempts_used).
    """
    import random
    delay = base_delay
    last_status = None
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            status, body = fn()
            last_status = status
            if status in TRANSIENT or (on_status and on_status(status, body)):
                if attempt < attempts:
                    time.sleep(delay + random.uniform(0, delay * 0.3))
                    delay = min(delay * 2, max_delay)
                    continue
                return False, body, status, attempt
            return True, body, status, attempt
        except Exception as e:
            last_err = e
            if attempt < attempts:
                time.sleep(delay + random.uniform(0, delay * 0.3))
                delay = min(delay * 2, max_delay)
    return False, last_err, None, attempts


def run_once(key: str, fn):
    """Idempotent side-effect: runs fn() only if key hasn't been stamped.
    Returns (ran, result)."""
    conn = _db()
    row = conn.execute("SELECT 1 FROM idempotency WHERE key=?", (key,)).fetchone()
    if row:
        conn.close()
        return False, None
    result = fn()
    conn.execute("INSERT OR REPLACE INTO idempotency (key, ts) VALUES (?, ?)",
                 (key, time.time()))
    conn.commit()
    conn.close()
    return True, result


def checkpoint(bot: str, step: str, data: dict | None = None):
    """Persist a step checkpoint (SQLite, ~ms). Crash-safe resume point."""
    conn = _db()
    conn.execute("INSERT OR REPLACE INTO checkpoints (bot, step, data, ts) VALUES (?,?,?,?)",
                 (bot, step, json.dumps(data or {}), time.time()))
    conn.commit()
    conn.close()


def last_checkpoint(bot: str):
    conn = _db()
    row = conn.execute("SELECT step, data, ts FROM checkpoints WHERE bot=? ORDER BY ts DESC LIMIT 1",
                       (bot,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"step": row[0], "data": json.loads(row[1] or "{}"), "ts": row[2]}


def beat(bot: str):
    """Heartbeat: stamp last_heartbeat for this bot (standby failover signal)."""
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.setdefault(bot, {})["last_heartbeat"] = time.time()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_stale(bot: str, max_idle_hours: float = 3.0) -> bool:
    """True if the bot's heartbeat is older than max_idle_hours (or absent)."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return True
    hb = state.get(bot, {}).get("last_heartbeat")
    if not hb:
        return True
    return time.time() - hb > max_idle_hours * 3600
