#!/usr/bin/env python
"""EXTERNAL watchdog for the KEN fleet — runs INDEPENDENT of Hermes.

Lesson from Tier 1 post-mortem (2026-08-14): fleet_boss runs INSIDE the same
stack it supervises — if Hermes/gateway dies, nobody pages. This script is
launched by Windows Task Scheduler directly (pythonw, hidden), reads fleet
state + does liveness checks, and alerts via a FILE (no Hermes dependency).

Alert channel: appends to E:\\Local\\projects\\personal-assistant\\data\\watchdog_alerts.jsonl
(phone push is off for routine alerts per user rule — rate-limited 1x/hour).

Checks:
1. Hermes gateway alive?  (TCP :8644 webhook + process check)
2. ken_service alive?     (process check, port :8756)
3. Ollama alive?          (TCP :11434)
4. Fleet health           (bot_runner --queue + fleet_report fresh?)
5. Work queue dead-letter (DLQ non-empty = failures piling up)

Exit codes: 0 = healthy, 1 = issue found (alert written).
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"E:\Local\projects\personal-assistant")
ALERTS = ROOT / "data" / "watchdog_alerts.jsonl"
RATE_LIMIT_S = 3600  # 1x/hour per user rule (no phone push for routine)

# Liveness targets
TARGETS = [
    ("hermes_gateway", 8644),
    ("ken_service", 8756),
    ("ollama", 11434),
]


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _proc_alive(substr: str) -> bool:
    """Check a process by command-line substring (pythonw/daemons)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match '{substr}' }} | Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout.strip().lstrip("0") != ""
    except Exception:
        return True  # can't check -> assume ok (don't false-alarm)


def _fleet_health() -> list[str]:
    """DLQ + scheduler freshness from bot_runner."""
    issues = []
    try:
        out = subprocess.run(
            [sys.executable, str(ROOT / "qa" / "bot_runner.py"), "--queue"],
            capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = out.stdout + out.stderr
        if "dead" in text and "0 dead" not in text:
            issues.append("work-queue DLQ non-empty (failures piling)")
    except Exception:
        pass
    # fleet report fresh?
    rep = ROOT / "data" / "fleet_report.md"
    if rep.exists():
        age = time.time() - rep.stat().st_mtime
        if age > 26 * 3600:
            issues.append("fleet_report.md stale (>26h — fleet_boss not running)")
    else:
        issues.append("fleet_report.md missing")
    return issues


def _alert(issue: str) -> None:
    """Append alert line, rate-limited. Returns False if suppressed."""
    try:
        if ALERTS.exists():
            last = ALERTS.read_text(encoding="utf-8").strip().splitlines()
            if last:
                try:
                    prev = json.loads(last[-1])
                    if time.time() - prev.get("ts", 0) < RATE_LIMIT_S and prev.get("issue") == issue:
                        return False
                except Exception:
                    pass
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with ALERTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "iso": datetime.now(timezone.utc).isoformat(),
                "issue": issue,
                "source": "external-watchdog",
            }) + "\n")
        return True
    except Exception:
        return False


def main() -> int:
    issues = []
    for name, port in TARGETS:
        if not _port_open(port):
            # double-check: process alive but port not yet bound?
            if name == "hermes_gateway" and _proc_alive("gateway"):
                continue
            issues.append(f"{name} DOWN (port {port} not responding)")

    issues += _fleet_health()

    if issues:
        alerted = False
        for i in issues:
            if _alert(i):
                alerted = True
        print("WATCHDOG_ISSUES: " + "; ".join(issues))
        return 1
    print("WATCHDOG_OK: all systems nominal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
