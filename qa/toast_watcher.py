#!/usr/bin/env python3
"""Toast watcher — catches Windows notifications via the notification DB.

Toasts persist in wpndatabase.Notification even after they vanish from the
screen. Poll the DB every 1s, track the newest ArrivalTime, and print every
NEW notification: app (PrimaryId), payload text, time. This catches 1-second
popups that screenshots miss. Run indefinitely in the background.

Usage: python qa/toast_watcher.py [outfile.jsonl]
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

DB = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                  "Microsoft", "Windows", "Notifications", "wpndatabase.db")
OUTFILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "toast_watch.jsonl")


def strip_tags(payload) -> str:
    try:
        if isinstance(payload, bytes):
            s = payload.decode("utf-8", "ignore")
        elif payload is None:
            return ""
        else:
            s = str(payload)
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip()
    except Exception:
        return ""


def newest_ts(con) -> int:
    try:
        cur = con.cursor()
        cur.execute("SELECT MAX(ArrivalTime) FROM Notification")
        return cur.fetchone()[0] or 0
    except Exception:
        return 0


def main() -> int:
    print(f"toast watcher: db={DB}", flush=True)
    print(f"logging new toasts -> {OUTFILE}", flush=True)
    last = 0
    with open(OUTFILE, "a", encoding="utf-8") as f:
        while True:
            try:
                con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
                max_ts = newest_ts(con)
                if max_ts > last:
                    cur = con.cursor()
                    cur.execute(
                        """SELECT h.PrimaryId, n.Payload, n.ArrivalTime
                           FROM Notification n
                           LEFT JOIN NotificationHandler h
                             ON n.HandlerId = h.RecordId
                           WHERE n.ArrivalTime > ? ORDER BY n.ArrivalTime""",
                        (last,))
                    for app, payload, ts in cur.fetchall():
                        text = strip_tags(payload)
                        ev = {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "app": app,
                            "arrival": ts,
                            "text": text[:300],
                        }
                        f.write(json.dumps(ev) + "\n")
                        f.flush()
                        print(f"[{ev['ts'][11:19]}] APP={app} :: {text[:120]}",
                              flush=True)
                    last = max_ts
                con.close()
            except Exception as e:
                print(f"  (watcher err: {e})", flush=True)
            time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
