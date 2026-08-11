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

Supervisor loop: Task Scheduler only restarts a task that *fails*. A clean
uvicorn exit (e.g. someone hits Ctrl+C, or uvicorn decides to stop for any
reason) reads as success and leaves KEN dead. So we must loop forever,
restarting uvicorn no matter how it exits.
"""
from __future__ import annotations

import datetime
import errno
import os
import sys
import time
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "data" / "ken_service.log"
HOST = "127.0.0.1"
PORT = int(os.environ.get("KEN_PORT", "8756"))
MAX_LOG_BYTES = int(os.environ.get("KEN_LOG_MAX_BYTES", "2000000"))
STOP_SENTINEL = BASE_DIR / "data" / "ken_service.stop"

BACKOFF_START = 5.0
BACKOFF_CAP = 60.0
STABLE_UPTIME = 60.0


def _log(message: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def _rotate_log() -> None:
    """Rotate the log at startup only. Never let a rotation failure kill us."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            # On Windows the file may be open by another process (or ourselves
            # from a previous run) — replace can raise, so swallow it.
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.old"))
    except Exception:
        pass


def _redirect_output() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log()
    stream = LOG_FILE.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def _run_once() -> float:
    """Run uvicorn once. Return seconds the process stayed up (for backoff)."""
    import uvicorn

    started = time.monotonic()
    try:
        _log(f"Starting uvicorn on {HOST}:{PORT} (pid {os.getpid()})")
        uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
        uptime = time.monotonic() - started
        _log(f"uvicorn exited cleanly after {uptime:.1f}s")
    except KeyboardInterrupt:
        _log("KeyboardInterrupt received, stopping")
        raise
    except SystemExit as exc:
        # NOT a deliberate stop. uvicorn handles bind failures itself: it logs
        # "[Errno 10048] error while attempting to bind" and calls sys.exit(1),
        # so a port conflict never reaches the OSError branch below. Re-raising
        # here made the supervisor quit on exactly the failure it exists to
        # survive. Deliberate stops come from the sentinel file, not SystemExit.
        uptime = time.monotonic() - started
        code = exc.code if exc.code is not None else 0
        _log(f"uvicorn called sys.exit({code}) after {uptime:.1f}s - will retry")
        return uptime
    except OSError as exc:
        uptime = time.monotonic() - started
        if getattr(exc, "winerror", None) == 10048 or exc.errno == errno.EADDRINUSE:
            _log(f"Port {PORT} in use (WSAEADDRINUSE) - a stale instance may exist. Retrying.")
        else:
            _log(f"OSError after {uptime:.1f}s: {exc}")
            traceback.print_exc()
        return uptime
    except Exception as exc:
        uptime = time.monotonic() - started
        _log(f"uvicorn crashed after {uptime:.1f}s: {exc}")
        traceback.print_exc()
        return uptime
    return time.monotonic() - started


def main() -> int:
    _redirect_output()
    os.chdir(BASE_DIR)
    sys.path.insert(0, str(BASE_DIR))

    _log(f"KEN service starting (pid {os.getpid()})")

    if STOP_SENTINEL.exists():
        _log(f"Stop sentinel present at {STOP_SENTINEL}, exiting")
        return 0

    backoff = BACKOFF_START

    while True:
        if STOP_SENTINEL.exists():
            _log(f"Stop sentinel present at {STOP_SENTINEL}, exiting")
            return 0

        # _run_once re-raises these; catching them here is what makes a
        # deliberate stop exit 0. Uncaught, they'd escape main(), skip
        # sys.exit(main()), and hand Task Scheduler a failure exit code --
        # which it would then dutifully "restart" from.
        try:
            uptime = _run_once()
        except (KeyboardInterrupt, SystemExit):
            _log("Interrupted, exiting cleanly")
            return 0

        if uptime > STABLE_UPTIME:
            backoff = BACKOFF_START
            _log("Run stayed up >60s, backoff reset to 5s")
        else:
            _log(f"Run only lasted {uptime:.1f}s, backing off {backoff}s")
            try:
                time.sleep(backoff)
            except KeyboardInterrupt:
                _log("KeyboardInterrupt during backoff, stopping")
                return 0
            backoff = min(backoff * 2, BACKOFF_CAP)
            _log(f"Next backoff will be {backoff}s")


if __name__ == "__main__":
    sys.exit(main())
