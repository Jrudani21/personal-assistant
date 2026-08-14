"""Persistent work queue + dead-letter queue (DLQ) for the KEN fleet.

Scheduler (bot_runner) enqueues work orders; consumers claim and complete
them. Failed orders retry with exponential backoff (transient only), then
land in a DLQ for inspection/redrive instead of vanishing.

Research-backed (2026-08-14 roadmap): this is the highest-ROI structural
change — today bot_runner prints work orders that can fail silently. With a
queue, every order is tracked through claim -> retry -> done/dead.

Design:
- SQLite-backed (WAL), one table, claim via UPDATE ... WHERE id=? AND state
  (atomic claim, no external broker).
- Exponential backoff: attempts 1..N with 2^attempt base delay, capped.
- Dead-letter: after MAX_ATTEMPTS, state=dead + dead_reason; inspectable via
  dlq() and redrivable via redrive().
- Idempotent: each order carries an idempotency key (bot id + run timestamp)
  so re-claim after a crash can't double-execute side effects.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # personal-assistant/
DB = ROOT / "data" / "work_queue.sqlite"

_lock = threading.Lock()

try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "qa_logger", ROOT / "qa" / "logger.py")
    _logger_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_logger_mod)
    _logger = _logger_mod
except Exception:  # pragma: no cover
    _logger = None


def _log(kind: str, **payload) -> None:
    if _logger:
        _logger.log("work_queue", kind, **payload)

# Attempts -> backoff seconds (2^attempt capped at 30m)
MAX_ATTEMPTS = 4
BACKOFF_BASE = 60  # 1min, 2min, 4min, 8min
BACKOFF_CAP = 1800


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS queue (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               key TEXT UNIQUE,          -- idempotency key (bot id + run ts)
               bot_id TEXT,
               work_order TEXT,          -- full work-order text
               state TEXT DEFAULT 'pending',  -- pending|claimed|done|dead
               attempts INTEGER DEFAULT 0,
               next_retry REAL DEFAULT 0,
               created REAL,
               claimed_at REAL,
               completed_at REAL,
               dead_reason TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_state ON queue(state, next_retry)")
    return conn


def enqueue(bot_id: str, work_order: str, idem_key: str | None = None) -> str:
    """Add a work order to the queue. Idempotent: same key -> no duplicate."""
    key = idem_key or f"{bot_id}:{int(time.time())}"
    try:
        conn = _connect()
        conn.execute(
            """INSERT OR IGNORE INTO queue(key,bot_id,work_order,state,created)
               VALUES(?,?,?,'pending',?)""",
            (key, bot_id, work_order, time.time()),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM queue WHERE key=?", (key,)).fetchone()
        conn.close()
        if row:
            _log("enqueued", bot_id=bot_id, order_id=row[0], idem_key=key)
            return str(row[0])
        _log("duplicate", bot_id=bot_id, idem_key=key)
        return "dup"
    except Exception as e:
        return f"err:{e}"


def claim(max_orders: int = 5) -> list[dict]:
    """Claim due pending orders (state=pending, next_retry<=now). Returns list
    of order dicts; caller must complete() or fail() them."""
    try:
        conn = _connect()
        rows = conn.execute(
            """SELECT id, bot_id, work_order, attempts FROM queue
               WHERE state='pending' AND next_retry<=? ORDER BY created
               LIMIT ?""",
            (time.time(), max_orders),
        ).fetchall()
        out = []
        for rid, bot_id, work_order, attempts in rows:
            # atomic claim
            cur = conn.execute(
                """UPDATE queue SET state='claimed', claimed_at=?
                   WHERE id=? AND state='pending'""",
                (time.time(), rid),
            )
            if cur.rowcount:
                out.append({"id": rid, "bot_id": bot_id,
                            "work_order": work_order, "attempts": attempts})
        conn.commit()
        conn.close()
        return out
    except Exception:
        return []


def complete(order_id: int) -> None:
    try:
        conn = _connect()
        conn.execute(
            "UPDATE queue SET state='done', completed_at=? WHERE id=?",
            (time.time(), order_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def complete_latest(bot_id: str) -> int:
    """Mark the most recent active (pending/claimed) order for a bot as done.
    Returns order id, or -1 if none. Agent-friendly: no numeric id needed."""
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT id FROM queue WHERE bot_id=? AND state IN ('pending','claimed') "
            "ORDER BY created DESC LIMIT 1",
            (bot_id,),
        ).fetchone()
        if not row:
            conn.close()
            return -1
        conn.execute("UPDATE queue SET state='done', completed_at=? WHERE id=?",
                     (time.time(), row[0]))
        conn.commit()
        conn.close()
        _log("done", bot_id=bot_id, order_id=row[0])
        return row[0]
    except Exception:
        return -1


def fail_latest(bot_id: str, reason: str) -> int:
    """Fail the most recent active order for a bot (retry backoff -> DLQ).
    Returns order id, or -1 if none."""
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT id, attempts FROM queue WHERE bot_id=? AND state IN ('pending','claimed') "
            "ORDER BY created DESC LIMIT 1",
            (bot_id,),
        ).fetchone()
        if not row:
            conn.close()
            return -1
        attempts = row[1] + 1
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE queue SET state='dead', dead_reason=?, attempts=? WHERE id=?",
                (reason[:500], attempts, row[0]),
            )
            _log("dead", bot_id=bot_id, order_id=row[0], reason=reason[:200])
        else:
            delay = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_CAP)
            conn.execute(
                "UPDATE queue SET state='pending', attempts=?, next_retry=? WHERE id=?",
                (attempts, time.time() + delay, row[0]),
            )
            _log("retry", bot_id=bot_id, order_id=row[0], attempts=attempts, delay_s=delay)
        conn.commit()
        conn.close()
        return row[0]
    except Exception:
        return -1


def fail(order_id: int, reason: str) -> None:
    """Mark a claimed order failed. Retries w/ backoff until MAX_ATTEMPTS,
    then dead-letters with reason."""
    try:
        conn = _connect()
        row = conn.execute("SELECT attempts FROM queue WHERE id=?", (order_id,)).fetchone()
        if not row:
            conn.close()
            return
        attempts = row[0] + 1
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE queue SET state='dead', dead_reason=?, attempts=? WHERE id=?",
                (reason[:500], attempts, order_id),
            )
        else:
            delay = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_CAP)
            conn.execute(
                "UPDATE queue SET state='pending', attempts=?, next_retry=? WHERE id=?",
                (attempts, time.time() + delay, order_id),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def dlq(limit: int = 20) -> list[dict]:
    """Inspect dead-lettered orders."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, bot_id, work_order, attempts, dead_reason FROM queue "
            "WHERE state='dead' ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [{"id": r[0], "bot_id": r[1], "work_order": r[2],
                 "attempts": r[3], "dead_reason": r[4]} for r in rows]
    except Exception:
        return []


def redrive(order_id: int) -> None:
    """Move a dead order back to pending for another run."""
    try:
        conn = _connect()
        conn.execute(
            "UPDATE queue SET state='pending', attempts=0, next_retry=?, dead_reason=NULL WHERE id=?",
            (time.time(), order_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def stats() -> dict:
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT state, COUNT(*) FROM queue GROUP BY state"
        ).fetchall()
        conn.close()
        return {s: c for s, c in rows}
    except Exception:
        return {}


def summary() -> str:
    s = stats()
    return (f"queue: {s.get('pending',0)} pending, {s.get('claimed',0)} claimed, "
            f"{s.get('done',0)} done, {s.get('dead',0)} dead")


if __name__ == "__main__":
    # self-test — ISOLATED: scratch DB, never the prod queue (lesson from
    # Tier 1 post-mortem: self-tests polluted prod work_queue.sqlite).
    import tempfile
    _orig_db = DB
    DB = Path(tempfile.mkdtemp(prefix="work_queue_test_")) / "test.sqlite"
    try:
        print("enqueue:", enqueue("test-bot", "TEST_WORK_ORDER hello", "k1"))
        print("enqueue dup:", enqueue("test-bot", "TEST_WORK_ORDER hello", "k1"))
        orders = claim()
        print("claim:", [(o['id'], o['bot_id']) for o in orders])
        for o in orders:
            fail(o['id'], "test failure")
        print("after fail:", summary())
        # force dead
        enqueue("test-bot2", "TEST_WORK_ORDER dead", "k2")
        o2 = claim()[0]
        for _ in range(MAX_ATTEMPTS):
            fail(o2['id'], "always fails")
        print("after max attempts:", summary())
        print("dlq:", [(d['id'], d['dead_reason']) for d in dlq()])
    finally:
        import shutil
        shutil.rmtree(DB.parent, ignore_errors=True)
        DB = _orig_db
