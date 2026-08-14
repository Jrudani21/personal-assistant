"""Lightweight SQLite event bus for the KEN fleet (dependency chains).

Research-backed (2026-08-14 Tier 4): use event triggers ONLY for real
dependency chains (research -> qa -> report) needing sub-hour reaction; keep
schedule-driven as the backbone. This is a minimal publish/subscribe over
SQLite — no broker, no new deps, fits the existing fleet.

Usage:
    from qa import event_bus as eb
    eb.publish("research.done", {"bot": "weather-sweep", "report": "data/research/x/report.md"})
    for ev in eb.consume("research.done", after=last_ts):
        ...
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "event_bus.sqlite"
_lock = threading.Lock()

# Subscriptions: event_type -> callback (callable(event_dict) -> None)
_SUBS: dict[str, list] = {}


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               type TEXT,
               payload TEXT,
               ts REAL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts)")
    return conn


def publish(event_type: str, payload: dict | None = None) -> int:
    """Emit an event. Returns event id. Never raises."""
    try:
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO events(type, payload, ts) VALUES(?,?,?)",
            (event_type, json.dumps(payload or {}), time.time()),
        )
        conn.commit()
        eid = cur.lastrowid
        conn.close()
        with _lock:
            for cb in _SUBS.get(event_type, []):
                try:
                    cb({"id": eid, "type": event_type, "payload": payload or {}, "ts": time.time()})
                except Exception:
                    pass  # subscriber error never breaks publish
        return eid
    except Exception:
        return -1


def consume(event_type: str, after: float = 0, limit: int = 100) -> list[dict]:
    """Read events of a type after a timestamp (polling consumer)."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, type, payload, ts FROM events WHERE type=? AND ts>? ORDER BY ts LIMIT ?",
            (event_type, after, limit),
        ).fetchall()
        conn.close()
        return [{"id": r[0], "type": r[1], "payload": json.loads(r[2] or "{}"), "ts": r[3]}
                for r in rows]
    except Exception:
        return []


def subscribe(event_type: str, callback) -> None:
    """Register an in-process callback for an event type."""
    with _lock:
        _SUBS.setdefault(event_type, []).append(callback)


def prune(older_than_s: float = 7 * 24 * 3600) -> int:
    """Drop events older than the TTL (keep DB lean)."""
    try:
        conn = _connect()
        cur = conn.execute("DELETE FROM events WHERE ts<?", (time.time() - older_than_s,))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n
    except Exception:
        return 0


def stats() -> dict:
    try:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        by_type = dict(conn.execute("SELECT type, COUNT(*) FROM events GROUP BY type").fetchall())
        conn.close()
        return {"total": n, "by_type": by_type}
    except Exception:
        return {"total": 0, "by_type": {}}


if __name__ == "__main__":
    # self-test — ISOLATED (lesson: never pollute prod stores)
    import tempfile
    import shutil
    _orig = DB
    DB = Path(tempfile.mkdtemp(prefix="event_bus_test_")) / "test.sqlite"
    try:
        eid = publish("research.done", {"bot": "x"})
        print("published id:", eid)
        print("consume:", consume("research.done"))
        print("stats:", stats())
    finally:
        shutil.rmtree(DB.parent, ignore_errors=True)
        DB = _orig
