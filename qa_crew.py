#!/usr/bin/env python
"""KEN QA Crew runner.

Fires the full testing crew in the background:
  1. TESTER  - runs Playwright E2E against the live server, collects failures
  2. DEBUGGER- reads failures + server logs, finds root causes
  3. FRONTEND DEV - fixes frontend bugs (ken.html / qa)
  4. BACKEND DEV  - fixes backend bugs (server.py / assistant)
  5. VERIFIER- re-runs the tests, confirms green

Usage:
  python qa_crew.py               # full crew
  python qa_crew.py --test-only   # just run Playwright
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QA = ROOT / "qa"
LOG = ROOT / "data" / "qa_crew.log"

# Credentials come from the same place as the live server
USER = os.environ.get("KEN_QA_USER", "janak")
PASS = os.environ.get("KEN_QA_PASS", "")


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_playwright() -> tuple[bool, str]:
    """Run the E2E suite; return (passed, output)."""
    log("Running Playwright E2E suite...")
    env = dict(os.environ)
    if PASS:
        env["KEN_QA_USER"] = USER
        env["KEN_QA_PASS"] = PASS
    proc = subprocess.run(
        ["npx.cmd", "playwright", "test", "--reporter=list"],
        cwd=str(QA), env=env, capture_output=True, text=True, timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    log(f"Playwright exit={proc.returncode}")
    log(out[-2000:])
    return proc.returncode == 0, out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-only", action="store_true")
    args = parser.parse_args()

    if not PASS:
        log("KEN_QA_PASS not set — QA tests that log in will fail.")
        print("Set KEN_QA_PASS to the owner password before running the crew.")
        return 2

    ok, out = run_playwright()
    if args.test_only:
        return 0 if ok else 1

    if ok:
        log("ALL TESTS PASSED — no debug/fix crew needed.")
        return 0

    log("TESTS FAILED — dispatching debug/fix crew.")
    # The actual agent crew is orchestrated by Hermes (delegate_task from the
    # parent). This script's job is the deterministic test run + evidence.
    # Write the failure evidence for the crew to read.
    ev = ROOT / "data" / "qa_failures.json"
    ev.write_text(json.dumps({"user": USER, "passed": ok, "output_tail": out[-4000:]}), encoding="utf-8")
    log(f"Evidence written to {ev}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
