#!/usr/bin/env python
"""KEN bot config runner.

Deterministic scheduler + work-order printer. Reads data/bots.json, decides
which bots are DUE (by schedule), and prints a work order. An agent cron
reads the work order and executes it (research bots -> delegate_task crew;
qa bots -> run the script). NO LLM here — free.

Usage:
  python qa/bot_runner.py               # print work order for due bots
  python qa/bot_runner.py --list        # list all bots + their schedule
  python qa/bot_runner.py --force <id>  # force-run a bot (ignore schedule)
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS_FILE = ROOT / "data" / "bots.json"
STATE_FILE = ROOT / "data" / "bots_state.json"


def load_bots() -> list[dict]:
    try:
        return json.loads(BOTS_FILE.read_text(encoding="utf-8")).get("bots", [])
    except Exception:
        return []


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    ROOT.joinpath("data").mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_due(bot: dict, state: dict, now: datetime) -> bool:
    """Schedule-aware due check."""
    if not bot.get("enabled", True):
        return False
    sched = bot.get("schedule", "manual")
    if sched == "manual":
        return False
    if sched == "hourly":
        last = state.get(bot["id"], {}).get("last_run")
        if not last:
            return True
        return now - datetime.fromisoformat(last) >= timedelta(hours=1)
    if sched.startswith("weekly:"):
        # weekly:day:HH:MM
        try:
            _, day, hhmm = sched.split(":")
            hh, mm = map(int, hhmm.split(":"))
        except ValueError:
            return False
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        if now.strftime("%a").lower() != day:
            return False
        due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < due_at:
            return False
        last = state.get(bot["id"], {}).get("last_run")
        if not last:
            return True
        return datetime.fromisoformat(last) < due_at
    if sched.startswith("daily:"):
        try:
            _, hhmm = sched.split(":")
            hh, mm = map(int, hhmm.split(":"))
        except ValueError:
            return False
        due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < due_at:
            return False
        last = state.get(bot["id"], {}).get("last_run")
        if not last:
            return True
        return datetime.fromisoformat(last) < due_at
    return False


def work_order(bot: dict, force: bool = False) -> str:
    """Print the execution instruction for one bot."""
    bid = bot["id"]
    btype = bot["type"]
    if btype == "qa":
        return (f"QA_BOT {bid}: run `python {bot['runner']}` "
                f"against {bot.get('target', 'sandbox')}. Rules: BOT_RULES.md §2 (no LLM).")
    if btype in ("research", "watch"):
        topic = bot.get("topic", "")
        depth = bot.get("depth", "medium")
        n = bot.get("subagents", 3)
        sources = ", ".join(bot.get("sources", ["web_search"]))
        out = bot.get("output", "data/research/")
        return (
            f"RESEARCH_BOT {bid}: deep research on '{topic}' "
            f"(depth={depth}, subagents={n}, sources={sources}).\n"
            f"  PATTERN (Anthropic orchestrator-worker):\n"
            f"  1. LEAD: decompose topic into {n} independent subtasks; each subagent "
            f"spec = objective + output format (markdown file) + tools/sources + task boundary.\n"
            f"  2. SPAWN {n} parallel subagents via delegate_task; each writes findings to "
            f"{out}{bid}/subagent-<n>.md (filesystem output, no game-of-telephone).\n"
            f"  3. SYNTHESIZE: lead reads all files, writes {out}{bid}/report.md with "
            f"per-claim citations (grounded-citations).\n"
            f"  4. SAVE: append a link in brain/notes or the daily note. Mark state done."
        )
    return f"BOT {bid}: unknown type {btype}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", metavar="ID")
    ap.add_argument("--dry-run", action="store_true", help="print due bots without marking run")
    args = ap.parse_args()

    bots = load_bots()
    if not bots:
        print("NO_BOTS_CONFIGURED (data/bots.json missing/empty)")
        return 0

    if args.list:
        for b in bots:
            mark = "ON " if b.get("enabled") else "off"
            print(f"[{mark}] {b['id']:<16} {b['type']:<10} schedule={b.get('schedule','manual'):<18} {b.get('name','')}")
        return 0

    now = datetime.now()
    state = load_state()

    due = []
    if args.force:
        bot = next((b for b in bots if b["id"] == args.force), None)
        if not bot:
            print(f"NO_BOT {args.force}")
            return 1
        due = [bot]
    else:
        due = [b for b in bots if is_due(b, state, now)]

    if not due:
        print("NO_BOTS_DUE")
        return 0

    for bot in due:
        print(work_order(bot, force=bool(args.force)))
        if not args.dry_run:
            state.setdefault(bot["id"], {})["last_run"] = now.isoformat()
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
