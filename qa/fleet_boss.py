#!/usr/bin/env python
"""KEN Ops Boss — fleet coordinator (deterministic core, LLM summary optional).

Reads data/bots.json + data/bots_state.json, checks every bot's freshness
against its schedule, flags stale/over-budget/enabled-but-never-run bots,
and produces a daily fleet report at data/fleet_report.md (appended to the
brain daily note by the agent cron). Deterministic = free.

Usage:
  python qa/fleet_boss.py                 # run oversight, print report
  python qa/fleet_boss.py --json          # machine-readable report
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS = ROOT / "data" / "bots.json"
STATE = ROOT / "data" / "bots_state.json"
REPORT = ROOT / "data" / "fleet_report.md"


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def freshness_ok(bot: dict, state: dict, now: datetime) -> tuple[bool, str]:
    """A bot is 'fresh enough' if it ran within the window its schedule implies."""
    sched = bot.get("schedule", "manual")
    last = (state.get(bot["id"]) or {}).get("last_run")
    if sched == "manual":
        return True, "manual (on-demand)"
    if not last:
        return False, "never ran"
    last_dt = datetime.fromisoformat(last)
    if sched == "hourly":
        ok = now - last_dt < timedelta(hours=3)
        return ok, f"last {last_dt:%m-%d %H:%M}"
    if sched.startswith("daily"):
        ok = now - last_dt < timedelta(hours=30)
        return ok, f"last {last_dt:%m-%d %H:%M}"
    if sched.startswith("weekly"):
        ok = now - last_dt < timedelta(days=8)
        return ok, f"last {last_dt:%m-%d %H:%M}"
    return True, "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fleet", default="", help="review only one fleet (qa/research/ops)")
    args = ap.parse_args()

    data = load_json(BOTS, {"fleets": [], "bots": []})
    bots = data.get("bots", [])
    fleets = data.get("fleets", [])
    if args.fleet:
        fleets = [f for f in fleets if f["id"] == args.fleet]
        member_ids = {m for f in fleets for m in f.get("members", [])}
        bots = [b for b in bots if b["id"] in member_ids]
    state = load_json(STATE, {})
    now = datetime.now()

    fleet_report = []
    issues = []
    # Heartbeat check: any bot with a standby_for counterpart whose heartbeat
    # is stale (or missing) gets flagged — the standby takes over (big-company
    # failover: one hot spare per fleet on heartbeat loss).
    from fleet_common import is_stale
    for fleet in fleets:
        members = [b for b in bots if b["id"] in fleet.get("members", [])]
        rows = []
        for b in members:
            ok, detail = freshness_ok(b, state, now)
            budget = b.get("budget", {})
            max_runs = budget.get("max_runs_per_day", 99)
            runs = (state.get(b["id"]) or {}).get("runs_today", 0)
            over = runs > max_runs
            flags = []
            if not ok:
                flags.append("STALE")
                issues.append(f"{b['id']}: {detail}")
            if over:
                flags.append(f"OVER-BUDGET ({runs}/{max_runs})")
                issues.append(f"{b['id']}: over budget {runs}/{max_runs}")
            if not b.get("enabled", True):
                flags.append("disabled")
            # Heartbeat loss -> standby takeover signal
            if b.get("standby_for"):
                primary = b["standby_for"]
                if is_stale(primary, max_idle_hours=3.0):
                    flags.append("TAKEOVER")
                    issues.append(f"{b['id']}: taking over for {primary} (heartbeat lost)")
            rows.append(f"- **{b.get('name', b['id'])}** `{b['id']}` — {detail}"
                        + (f" ⚠️ {', '.join(flags)}" if flags else " ✅"))
        fleet_report.append(f"### {fleet.get('name', fleet['id'])} "
                            f"({len(rows)} bots)\n" + "\n".join(rows))

    # Coordinator summary
    summary_lines = []
    summary_lines.append(f"# Fleet Report — {now:%Y-%m-%d %H:%M}")
    total = len(bots)
    enabled = sum(1 for b in bots if b.get("enabled", True))
    stale = len([i for i in issues if "STALE" in i])
    fleet_word = "fleet" if len(fleets) == 1 else "fleets"
    summary_lines.append(f"- **{total} bots** registered, **{enabled} enabled** "
                         f"across **{len(fleets)} {fleet_word}**.")
    if issues:
        summary_lines.append(f"- ⚠️ **{len(issues)} issue(s)**:")
        for i in issues:
            summary_lines.append(f"  - {i}")
    else:
        summary_lines.append("- ✅ All bots fresh and within budget.")

    report = "\n".join(summary_lines + [""] + fleet_report) + "\n"

    if args.json:
        print(json.dumps({
            "bots_total": total, "enabled": enabled, "fleets": len(fleets),
            "issues": issues, "report": report,
        }, indent=2))
        return 0

    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"(report written to {REPORT})")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
