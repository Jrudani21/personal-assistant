#!/usr/bin/env python
"""Scheduler work-order printer for cron monitor mode (stable output).

Runs qa/bot_runner.py --dry-run in the personal-assistant repo and emits
STABLE text (timestamps stripped), so a Hermes cron monitor_script can hash
it: UNCHANGED output suppresses the agent run (0 tokens); CHANGED output
fires the agent to execute the work order, which records completion via
`python qa/bot_runner.py --mark-done <id>`.

Self-contained (no repo imports) so it can live in the Hermes cron scripts
dir. Source of truth: E:\Local\projects\personal-assistant\qa\sched_workorder.py
"""
import subprocess
import sys
from pathlib import Path

PA_DIR = Path(r"E:\Local\projects\personal-assistant")


def main() -> int:
    if not (PA_DIR / "qa" / "bot_runner.py").exists():
        print("NO_BOTS_CONFIGURED (repo missing)")
        return 0
    try:
        out = subprocess.run(
            [sys.executable, "qa/bot_runner.py", "--dry-run"],
            cwd=str(PA_DIR), capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception as e:  # pragma: no cover
        print(f"SCHED_ERROR {e}")
        return 1
    for line in out.splitlines():
        # Strip the minute-level timestamp so the hash is stable within a window.
        if line.startswith("PEAK_PRICE "):
            print("PEAK_PRICE_DEFERRED")
        else:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
