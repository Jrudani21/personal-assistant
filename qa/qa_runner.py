#!/usr/bin/env python
"""KEN continuous QA bot (deterministic, NO LLM = free to run hourly).

Runs the Playwright suite + a few API probes against the SANDBOX, appends
structured results to data/qa_buglog.jsonl, and tracks known-vs-new failures
so the off-peak fix crew only sees genuinely NEW bugs.

Exit/stdout contract (for cron no_agent mode):
  - all green          -> empty stdout (silent tick)
  - known failures only-> empty stdout (silent tick)
  - NEW failure(s)     -> prints a short alert line (delivered)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root
QA = ROOT / "qa"
DATA = ROOT / "data"
BUGLOG = DATA / "qa_buglog.jsonl"
STATE = DATA / "qa_state.json"
SANDBOX = ROOT / "sandbox.py"
PORT = int(os.environ.get("KEN_SANDBOX_PORT", "8766"))
BASE = f"http://127.0.0.1:{PORT}"
USER = os.environ.get("KEN_QA_USER", "janak")
PASS = os.environ.get("KEN_QA_PASS", "sandbox-pass")


def log(msg: str) -> None:
    print(msg, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_sandbox() -> bool:
    """Start the sandbox if it's not answering; return True when healthy."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        pass
    subprocess.run([sys.executable, str(SANDBOX), "start", "--port", str(PORT)],
                   cwd=str(ROOT), capture_output=True, timeout=60)
    for _ in range(12):
        time.sleep(5)
        try:
            import urllib.request
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
    return False


def run_playwright() -> dict:
    """Run the E2E suite with the JSON reporter; return structured results."""
    out_file = DATA / "qa_last_run.json"
    env = dict(os.environ)
    env["KEN_QA_USER"] = USER
    env["KEN_QA_PASS"] = PASS
    env["KEN_BASE_URL"] = BASE
    subprocess.run(
        ["npx", "playwright", "test", "--reporter=json",
         f"--output={out_file}"],
        cwd=str(QA), env=env, capture_output=True, text=True, timeout=600,
    )
    try:
        data = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    suites = data.get("suites", [])
    tests = []
    def walk(s):
        for t in s.get("specs", []):
            for r in t.get("tests", []):
                tests.append(r)
        for c in s.get("suites", []):
            walk(c)
    for s in suites:
        walk(s)
    failures = []
    for t in tests:
        results = t.get("results", [{}])
        status = results[0].get("status") if results else "unknown"
        if status in ("failed", "timedOut"):
            failures.append({
                "test": t.get("title", "?"),
                "file": t.get("file", "?"),
                "error": (results[0].get("error") or {}).get("message", "")[:500],
            })
    return {"passed": sum(1 for t in tests if (t.get("results") or [{}])[0].get("status") == "passed"),
            "failed": len(failures),
            "failures": failures}


def run_api_probes() -> list[dict]:
    """A few cheap API sanity probes against the sandbox (login/users)."""
    import urllib.request, urllib.error, http.cookiejar
    issues = []
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    def req(path, method="GET", data=None):
        body = json.dumps(data).encode() if data else None
        r = urllib.request.Request(BASE + path, data=body,
                                   headers={"Content-Type": "application/json"}, method=method)
        try:
            with op.open(r) as x:
                return x.status, x.read().decode()[:200]
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:200]
    checks = [
        ("health", req("/api/health")[0] == 200),
        ("login-owner", req("/api/login", "POST", {"username": USER, "password": PASS})[0] == 200),
        ("whoami", req("/api/whoami")[0] == 200),
        ("users-list", req("/api/users")[0] == 200),
        ("bad-login-rejected", req("/api/login", "POST", {"username": USER, "password": "nope"})[0] == 401),
    ]
    for name, ok in checks:
        if not ok:
            issues.append({"probe": name, "error": f"check failed: {name}"})
    return issues


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_buglog(entry: dict) -> None:
    DATA.mkdir(exist_ok=True)
    with BUGLOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true")
    args = ap.parse_args()

    if not ensure_sandbox():
        log("QA bot: SANDBOX FAILED TO START")
        return 1

    # API probes always run (cheap, no browser).
    probes = run_api_probes()

    if args.probe_only:
        if probes:
            log(f"QA bot: {len(probes)} API probe failure(s)")
            for p in probes:
                log("  " + p["error"])
            return 1
        return 0

    pw = run_playwright()
    all_failures = pw["failures"] + probes
    fingerprint = json.dumps(sorted(f["test"] if "test" in f else f["probe"] for f in all_failures))

    state = load_state()
    prev_fp = state.get("last_fp", "")
    known = set(state.get("known", []))

    new_fails = [f for f in all_failures
                 if (f.get("test") or f.get("probe")) not in known]
    known_now = sorted(f.get("test") or f.get("probe") for f in all_failures)

    entry = {
        "ts": _now(), "run_id": time.strftime("%Y%m%d%H%M%S"),
        "playwright_passed": pw["passed"], "playwright_failed": pw["failed"],
        "probe_failures": len(probes),
        "status": "green" if not all_failures else "fail",
        "failures": all_failures,
    }
    append_buglog(entry)
    save_state({"last_fp": fingerprint, "known": known_now, "last_ts": _now()})

    if not all_failures:
        return 0                      # silent tick
    if not new_fails:
        return 0                      # known failures only — silent tick
    # NEW failure: alert (this is what the off-peak crew will pick up).
    log(f"QA bot: {len(new_fails)} NEW failure(s) logged to qa_buglog.jsonl")
    for f in new_fails[:8]:
        log(f"  - {f.get('test') or f.get('probe')}: {(f.get('error') or '')[:120]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
