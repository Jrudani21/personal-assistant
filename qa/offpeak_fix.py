#!/usr/bin/env python
"""KEN off-peak bug-fix coordinator.

Reads data/qa_buglog.jsonl, collects all UNFIXED failures (status=fail with
fixed!=true), and prints a compact, self-contained work order. The off-peak
agent cron runs this, reads the work order, dispatches the fix crew, then
re-runs the suite. No LLM here — pure data aggregation, so this step is free.

Usage:
  python qa/offpeak_fix.py          # print open-bug work order
  python qa/offpeak_fix.py --mark-fixed <run_id>   # mark a run's bugs fixed
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUGLOG = ROOT / "data" / "qa_buglog.jsonl"


def load_entries() -> list[dict]:
    if not BUGLOG.exists():
        return []
    return [json.loads(line) for line in BUGLOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def open_bugs() -> list[dict]:
    """All failure entries not yet marked fixed (dedup by failure fingerprint)."""
    seen: dict[str, dict] = {}
    for e in load_entries():
        if e.get("status") != "fail":
            continue
        if e.get("fixed"):
            continue
        for f in e.get("failures", []):
            key = f.get("test") or f.get("probe") or "?"
            if key not in seen:   # keep the earliest occurrence
                seen[key] = {**f, "run_id": e.get("run_id"), "ts": e.get("ts")}
    return list(seen.values())


def mark_fixed(run_ids: list[str]) -> int:
    """Mark every entry whose run_id is in run_ids as fixed (append tombstone)."""
    lines = BUGLOG.read_text(encoding="utf-8").splitlines()
    out = []
    n = 0
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            out.append(line)
            continue
        if e.get("run_id") in run_ids and e.get("status") == "fail" and not e.get("fixed"):
            e["fixed"] = True
            e["fixed_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ")
            n += 1
        out.append(json.dumps(e))
    BUGLOG.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-fixed", nargs="*", default=None)
    a = ap.parse_args()

    if a.mark_fixed is not None:
        n = mark_fixed(a.mark_fixed)
        print(f"marked {n} bug entry(s) fixed")
        return 0

    bugs = open_bugs()
    if not bugs:
        print("NO_OPEN_BUGS")     # agent cron: nothing to do -> skip crew
        return 0

    print(f"OPEN_BUGS {len(bugs)}")
    for i, b in enumerate(bugs, 1):
        title = b.get("test") or b.get("probe") or "?"
        print(f"BUG{i}\t{title}\t{str(b.get('error') or '')[:300]}\tfrom_run={b.get('run_id')}")
    print("INSTRUCTIONS: fix each BUG above (read the test files / server code), "
          "restart the live service if server.py changed, re-run the suite, "
          "then mark fixed with --mark-fixed <run_id> per fixed run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
