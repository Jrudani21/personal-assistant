# SPEC — ken_service.py (supervised, always-on launcher for KEN)

Rewrite `ken_service.py`. Output the complete file, nothing else.

## Purpose

Headless entrypoint for the `JanakKEN` Windows scheduled task. Runs the KEN
FastAPI app (`server:app`) on `127.0.0.1:8756` forever, surviving crashes and
clean exits, with no console window.

## Hard constraints

- Runs under `pythonw.exe`, which supplies **no stdout/stderr handles**.
  Redirect `sys.stdout` and `sys.stderr` to a log file **before importing or
  starting uvicorn** — uvicorn logs to stdout during startup and dies
  instantly otherwise. This is the bug that made the task fail silently.
- Do not import or call anything from `app.py` (it opens a browser).
- Stdlib + uvicorn only. Python 3.12. Windows.
- `os.chdir(BASE_DIR)` and `sys.path.insert(0, BASE_DIR)` before starting the
  app; `server.py` resolves `data/` relative to itself.

## Behaviour to implement

1. **Supervisor loop.** `uvicorn.run()` returning normally must NOT end the
   process. Task Scheduler only restarts a task that *fails*; a clean exit
   reads as success and leaves KEN dead. Loop forever: run uvicorn, and when
   it returns or raises, log why and restart.

2. **Stop sentinel.** If `data/ken_service.stop` exists, log it and exit 0
   without starting. Check it at the top of every loop iteration so the loop
   can be broken deliberately. Delete-to-restart is not required.

3. **Backoff.** Restart delay starts at 5s and doubles to a 60s cap on
   consecutive failures; reset to 5s after any run that stayed up >60s.
   Prevents a tight crash-loop from spinning the CPU.

4. **Port-in-use is special.** `OSError` with `winerror == 10048` (or errno
   `EADDRINUSE`) means a stale instance still holds 8756. Log it explicitly
   as such, then retry with backoff — do not treat it as fatal.

5. **Log rotation.** Rotate `data/ken_service.log` to `.log.old` when it
   exceeds 2 MB, checked at startup only. Rotation must never crash the
   service: on Windows the file may be open, so `Path.replace` can raise —
   catch and continue with the existing file.

6. **Log lines** are timestamped `%Y-%m-%d %H:%M:%S`, and record: service
   start (with pid), each uvicorn start, each exit with reason/traceback, the
   chosen backoff delay, and stop-sentinel exit.

7. **Signals.** `KeyboardInterrupt` / `SystemExit` must exit the loop cleanly
   with code 0 rather than triggering a restart.

## Configuration

- `KEN_PORT` env var, default `8756`. Host is always `127.0.0.1`.
- `KEN_LOG_MAX_BYTES` env var, default `2_000_000`.

## Structure

Module-level constants, small named functions (`_rotate_log`,
`_redirect_output`, `_log`, `_run_once`, `main`), `if __name__ == "__main__":
sys.exit(main())`. Comment *why* for the non-obvious parts — the pythonw
stdout issue, and why a clean uvicorn exit must still restart. Do not comment
the obvious.
