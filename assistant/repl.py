"""Manages the persistent Python REPL subprocess used by the run_python tool.
One worker for the whole app (not scoped per-chat) — simplest useful version
for a single local user. Variables persist across calls until restart() is
called (explicitly, or automatically after a crash/timeout).

Windows has no select() on pipes, so reading with a timeout needs a
background thread pushing lines into a queue rather than a blocking read.
"""
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent / "data" / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)
WORKER_SCRIPT = Path(__file__).resolve().parent / "repl_worker.py"

_proc = None
_out_queue = None
_lock = threading.Lock()


def _reader_thread(proc, q):
    for line in proc.stdout:
        q.put(line)
    q.put(None)  # worker's stdout closed — process ended


def _ensure_started():
    global _proc, _out_queue
    if _proc is not None and _proc.poll() is None:
        return
    _proc = subprocess.Popen(
        [sys.executable, "-u", str(WORKER_SCRIPT)],
        cwd=WORKSPACE,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )
    _out_queue = queue.Queue()
    threading.Thread(target=_reader_thread, args=(_proc, _out_queue), daemon=True).start()


def run_persistent(code: str, timeout: float = 15.0) -> str:
    with _lock:
        _ensure_started()
        try:
            _proc.stdin.write(json.dumps({"code": code}) + "\n")
            _proc.stdin.flush()
        except (BrokenPipeError, OSError):
            restart()
            _ensure_started()
            _proc.stdin.write(json.dumps({"code": code}) + "\n")
            _proc.stdin.flush()

        try:
            line = _out_queue.get(timeout=timeout)
        except queue.Empty:
            restart()
            return f"Error: execution timed out ({timeout:g}s limit). Session was restarted — prior variables are gone."

        if line is None:
            restart()
            return "Error: Python session crashed. It has been restarted — prior variables are gone."

        response = json.loads(line)
        parts = []
        if response["stdout"]:
            parts.append(response["stdout"])
        if response["stderr"]:
            parts.append("[stderr]\n" + response["stderr"])
        if response["error"]:
            parts.append(response["error"])
        return "\n".join(parts).strip() or "(no output)"


def restart() -> str:
    global _proc, _out_queue
    if _proc is not None:
        try:
            _proc.stdin.close()
        except Exception:
            pass
        try:
            _proc.terminate()
        except Exception:
            pass
    _proc = None
    _out_queue = None
    return "Python session restarted. All prior variables are gone."


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None
