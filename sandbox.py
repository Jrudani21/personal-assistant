#!/usr/bin/env python
"""KEN sandbox launcher: run an isolated copy of the server on a test port
with its own data dir, so QA/testing never touches the live instance.

Usage:
  python sandbox.py start [--port 8766]   # start sandbox (background)
  python sandbox.py stop  [--port 8766]   # stop it
  python sandbox.py status [--port 8766]  # health check

The sandbox copies the live code (server.py, assistant/, ken.html, assets/)
into a temp sandbox dir and uses a SEPARATE data/ (fresh users: owner
janak / password from KEN_SANDBOX_PASS or 'sandbox-pass'). Token auth is
DISABLED (KEN_TOKEN="") so tests don't fight the auth gate, but the
username/password login still works — perfect for E2E.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX_ROOT = Path(os.environ.get("KEN_SANDBOX_DIR", str(Path(tempfile.gettempdir()) / "ken-sandbox")))


def _port_file(port: int) -> Path:
    return SANDBOX_ROOT / f"port-{port}.json"


def start(port: int) -> None:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    # Copy code (skip data/, .git, node_modules, dist)
    code = SANDBOX_ROOT / "code"
    if not (code / "server.py").exists():
        shutil.copytree(ROOT, code, ignore=shutil.ignore_patterns(
            "data", ".git", "node_modules", "dist", "__pycache__", ".pytest_cache", "logs", ".deepcode",
        ))
    # Fresh data dir with a known owner
    data = SANDBOX_ROOT / f"data-{port}"
    data.mkdir(exist_ok=True)
    users = data / ".ken_users.json"
    if not users.exists():
        import hashlib, secrets
        salt = secrets.token_bytes(16)
        h = hashlib.scrypt(b"sandbox-pass", salt=salt, n=2**14, r=8, p=1)
        users.write_text(json.dumps({
            "janak": {"pw": salt.hex() + "$" + h.hex(), "role": "owner", "created_at": time.time()}
        }, indent=2), encoding="utf-8")
    env = dict(os.environ)
    env["KEN_TOKEN"] = ""            # disable token auth for tests
    env["KEN_USERNAME"] = "janak"
    env["KEN_PASSWORD"] = "sandbox-pass"
    env["KEN_PORT"] = str(port)
    # CRITICAL: strip any venv contamination so the child uses the REAL
    # Python 3.12 install's site-packages (fastapi/pydantic live there).
    # The Hermes venv on PATH has a broken pydantic_core and would 500.
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    py = r"C:\Users\Janak's PC\AppData\Local\Programs\Python\Python312\pythonw.exe"
    proc = subprocess.Popen(
        [py, str(code / "ken_service.py")],
        cwd=str(code), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _port_file(port).write_text(json.dumps({"pid": proc.pid}), encoding="utf-8")
    print(f"sandbox starting on :{port} (pid {proc.pid})")


def stop(port: int) -> None:
    pf = _port_file(port)
    if pf.exists():
        pid = json.loads(pf.read_text(encoding="utf-8"))["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pf.unlink(missing_ok=True)
        print(f"sandbox on :{port} stopped")
    else:
        print(f"no sandbox on :{port}")


def status(port: int) -> None:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
            print(f":{port} -> {r.status} {r.read().decode()[:80]}")
    except Exception as e:
        print(f":{port} -> DOWN ({e})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "stop", "status"])
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    {"start": start, "stop": stop, "status": status}[a.action](a.port)
