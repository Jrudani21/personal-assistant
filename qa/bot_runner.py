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


def load_config() -> dict:
    """Full bots.json (fleets + bots) for coordinator lookups."""
    try:
        return json.loads(BOTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"fleets": [], "bots": []}


# ── DeepSeek pricing windows (UTC-5 = local) ─────────────────────────────
# From DeepSeek V4 launch (Jul 2026) + classic off-peak discounts:
#   EXPENSIVE (peak surcharge ~2x): 20:00-23:00 & 01:00-05:00 local
#   DISCOUNT (off-peak -50-75%):     11:30-19:30 local
#   NEUTRAL:                         everything else
# Heavy LLM bots should run in DISCOUNT or NEUTRAL, never EXPENSIVE.
EXPENSIVE = ((20, 23), (1, 5))   # (start_hour, end_hour) inclusive ranges
DISCOUNT = (11, 19)              # start, end hours (11:30-19:30 -> [11,19])


def price_window(now: datetime) -> str:
    """Return 'expensive' | 'discount' | 'neutral' for the given time."""
    h = now.hour
    for start, end in EXPENSIVE:
        # 01-05 wraps; 20-23 simple
        if start <= end:
            if start <= h <= end:
                return "expensive"
        else:
            if h >= start or h <= end:
                return "expensive"
    ds, de = DISCOUNT
    if ds <= h <= de:
        return "discount"
    return "neutral"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    ROOT.joinpath("data").mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_due(bot: dict, state: dict, now: datetime, config: dict | None = None) -> bool:
    """Schedule-aware due check, deferring LLM-heavy bots out of peak price.

    Standby/failover: a bot with `standby_for` set only runs when its primary
    has MISSED its window (so if the primary crashes, the spare takes over;
    otherwise the spare stays idle and costs nothing).
    """
    if not bot.get("enabled", True):
        return False
    # Standby bot: only run if the primary is stale.
    primary = bot.get("standby_for")
    if primary:
        prim_state = state.get(primary, {})
        last = prim_state.get("last_run")
        prim_sched = ""
        if config:
            for b in config.get("bots", []):
                if b.get("id") == primary:
                    prim_sched = b.get("schedule", "")
                    break
        if last:
            last_dt = datetime.fromisoformat(last)
            # Primary considered healthy if it ran within 2x its schedule gap.
            gap = 3 if prim_sched == "hourly" else (30 if prim_sched.startswith("daily") else 8 * 24)
            if now - last_dt < timedelta(hours=gap):
                return False   # primary is fine; spare stays idle
        return _sched_due(bot, state, now)
    # Deterministic bots (qa/health) cost nothing -> always run regardless of price.
    if bot.get("type") in ("qa", "health"):
        return _sched_due(bot, state, now)
    # TIME-SENSITIVE bots (live tracking: market scans, price monitors, ledgers)
    # run on schedule no matter the price — a missed tick costs more than tokens.
    if bot.get("time_sensitive"):
        return _sched_due(bot, state, now)
    # LLM-heavy bots (research/watch/coordinator/agent): NEVER run in the expensive window.
    if bot.get("type") in ("research", "watch", "coordinator", "agent") and price_window(now) == "expensive":
        return False
    return _sched_due(bot, state, now)


def _sched_due(bot: dict, state: dict, now: datetime) -> bool:
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
            _, day, hhmm = sched.split(":", 2)
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
            _, hhmm = sched.split(":", 1)
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
    if btype == "coordinator":
        fleet = bot.get("fleet", "")
        farg = f" --fleet {fleet}" if fleet else ""
        return (f"COORDINATOR_BOT {bid}: run `python {bot.get('runner', 'qa/fleet_boss.py')}{farg}` "
                f"— fleet oversight for the {fleet or 'whole'} fleet. If it reports issues, escalate per "
                f"BOT_RULES §3 (deliver the fleet report to the owner; do NOT auto-fix).")
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
    if btype == "agent":
        # 500-AI-Agents-Projects script agent: run the generic wrapper against
        # the DeepSeek-swapped agent in the sibling repo.
        agent_dir = bot.get("agent_dir", "")
        agent_args = bot.get("agent_args", "")
        cmd = f"python qa/run_500agent.py {agent_dir} {agent_args}".strip()
        return (
            f"AGENT_BOT {bid}: run `{cmd}` (500-AI-Agents repo, DeepSeek-v4-flash). "
            f"Output to data/agents/{agent_dir}.log. Rules: BOT_RULES §2 (LLM billed via DeepSeek)."
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
    bots_cfg = load_config()

    # Peak-price reminder: even if no bot is due, tell the scheduler agent
    # why heavy work is parked (it may want to warn the user / log it).
    win = price_window(now)
    due = []
    if args.force:
        bot = next((b for b in bots if b["id"] == args.force), None)
        if not bot:
            print(f"NO_BOT {args.force}")
            return 1
        due = [bot]
    else:
        due = [b for b in bots if is_due(b, state, now, bots_cfg)]

    if not due:
        if win == "expensive":
            print(f"PEAK_PRICE {now:%H:%M} — LLM bots deferred until off-peak "
                  f"(discount 11:30-19:30, neutral 23:00-01:00/05:00-11:30). "
                  f"Nothing ran to save ~2x tokens.")
        else:
            print("NO_BOTS_DUE")
        return 0

    for bot in due:
        # Approval gate: bots flagged needs_approval never auto-run. They emit
        # a PENDING_APPROVAL work order so the agent can deliver it to the
        # owner's phone (Telegram/WhatsApp once wired) and wait for yes/no.
        # If the bot's fleet coordinator has can_approve, the coordinator may
        # pre-approve ROUTINE requests (low-risk, within budget); anything
        # unusual still goes to the human.
        if bot.get("needs_approval") and not args.force:
            coord = None
            fleet_id = bot.get("fleet", "")
            for f in bots_cfg.get("fleets", []):
                if f.get("id") == fleet_id:
                    coord = next((b for b in bots_cfg.get("bots", [])
                                  if b.get("id") == f.get("coordinator")), None)
                    break
            if coord and coord.get("can_approve") and bot.get("approval_level") == "routine":
                if not args.dry_run:
                    state.setdefault(bot["id"], {})["last_run"] = now.isoformat()
                print(f"COORD_APPROVED {bot['id']}: {coord['id']} pre-approved routine "
                      f"'{bot.get('approval_note', '')}' — running.")
                continue
            print(f"PENDING_APPROVAL {bot['id']}: {bot.get('name', bot['id'])}"
                  f" — {bot.get('approval_note', 'admin action requested')}."
                  f" Approve? (deliver this to the owner's phone, wait for reply)")
            if not args.dry_run:
                state.setdefault(bot["id"], {})["last_run"] = now.isoformat()
            continue
        print(work_order(bot, force=bool(args.force)))
        if not args.dry_run:
            state.setdefault(bot["id"], {})["last_run"] = now.isoformat()
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
