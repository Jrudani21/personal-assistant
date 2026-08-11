"""Headless launcher for KEN, for the JanakKEN scheduled task.

Why this exists instead of pointing the task straight at uvicorn:
`pythonw.exe` (needed so no console window appears at logon) provides no
stdout/stderr handles. uvicorn's logging writes to stdout on startup, which
makes it die immediately under pythonw — the task then reports
LastTaskResult=1 with nothing to show for it. Redirecting both streams to a
real file first fixes that and leaves a log to debug from.

Not the same as app.py: that one opens a browser, which is wrong for a
service.

    pythonw.exe ken_service.py
"""
from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "data" / "ken_service.log"
HOST = "127.0.0.1"
PORT = int(os.environ.get("KEN_PORT", "8756"))
MAX_LOG_BYTES = 2_000_000


def _redirect_output() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep the log from growing without bound across restarts.
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.old"))
    except Exception:
        pass
    stream = LOG_FILE.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== KEN service starting {stamp} (pid {os.getpid()}) ===")


def main() -> int:
    _redirect_output()
    os.chdir(BASE_DIR)          # server.py resolves data/ relative to itself
    sys.path.insert(0, str(BASE_DIR))
    try:
        import uvicorn
        uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
