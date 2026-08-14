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
  python qa/bot_runner.py --mark-done <id> [<id> ...]  # record true completion time
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS_FILE = ROOT / "data" / "bots.json"
STATE_FILE = ROOT / "data" / "bots_state.json"

# Work queue + DLQ: work orders are enqueued (not just printed) so failures
# retry with backoff and dead-letter instead of vanishing. Imported lazily
# to keep bot_runner importable without the queue module.
_queue = None


def _get_queue():
    global _queue
    if _queue is None:
        import importlib
        _queue = importlib.import_module("work_queue")  # qa/work_queue.py
    return _queue


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


def _audit_approval(bot: dict, decision: str, reason: str = "") -> None:
    """Append an approval decision to data/approvals.jsonl (audit trail).

    Every approval/denial/override is logged with timestamp, bot, decision,
    and reason — the fleet's audit event log (research-backed 2026-08-14).
    Never raises.
    """
    try:
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        entry = {
            "ts": _dt.now(_tz.utc).isoformat(),
            "bot": bot.get("id", "?"),
            "decision": decision,
            "reason": reason,
            "approval_note": bot.get("approval_note", ""),
        }
        log = ROOT / "data" / "approvals.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass


def _window_key(bot: dict, now: datetime) -> str:
    """Stable idempotency key per schedule window: a bot enqueued for the same
    window (day for daily/weekly, hour for hourly) maps to ONE key, so repeat
    monitor ticks (15min) never duplicate the order. --force runs use
    timestamp keys (each force is distinct)."""
    sched = bot.get("schedule", "manual")
    if sched.startswith("hourly"):
        return f"{bot['id']}:{now:%Y%m%d-%H}"
    if sched.startswith(("daily:", "weekly:")):
        return f"{bot['id']}:{now:%Y%m%d}"
    return f"{bot['id']}:{now.isoformat()}"  # manual/force -> unique per run


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
        # the DeepSeek-swapped agent in the sibling repo. Local-tier bots carry
        # an "env" dict (e.g. DEEPSEEK_API_BASE=ollama) that is forwarded via
        # --env so they run on qwen3:8b for zero tokens.
        agent_dir = bot.get("agent_dir", "")
        agent_args = bot.get("agent_args", "")
        env_opt = ""
        if bot.get("env"):
            pairs = " ".join(f"{k}={v}" for k, v in bot["env"].items())
            env_opt = f" --env {pairs}"
        cmd = f"python qa/run_500agent.py {agent_dir} {agent_args}{env_opt}".strip()
        tier = bot.get("tier", "flash")
        vstep = ""
        if bot.get("verify") == "review":
            vstep = (
                f"\n  VERIFY: after the run, have a flash-tier model review {bot.get('output', 'the output')} "
                f"for correctness/quality before it is used. If the review fails, do NOT apply — re-run or escalate."
            )
        return (
            f"AGENT_BOT {bid}: run `{cmd}` (500-AI-Agents repo, tier={tier}). "
            f"Output to data/agents/{agent_dir}.log. Rules: BOT_RULES §2 (LLM billed via DeepSeek unless tier=local)."
            f"{vstep}"
        )
    return f"BOT {bid}: unknown type {btype}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", metavar="ID")
    ap.add_argument("--dry-run", action="store_true", help="print due bots without marking run")
    ap.add_argument("--mark-done", nargs="+", metavar="ID",
                    help="record last_run=now for completed bot id(s) and exit")
    ap.add_argument("--queue", action="store_true",
                    help="show work-queue + DLQ status and exit")
    ap.add_argument("--enqueue-due", action="store_true",
                    help="enqueue due work orders into the queue (side effect, "
                         "no stdout) — called by sched_workorder; window-stable "
                         "keys so repeat monitor ticks don't duplicate")
    ap.add_argument("--queue-done", metavar="ID",
                    help="mark the most recent active queue order for bot ID as done")
    ap.add_argument("--queue-fail", nargs="+", metavar=("ID", "REASON"),
                    help="fail the most recent active queue order for bot ID (retry -> DLQ)")
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

    if args.queue:
        try:
            q = _get_queue()
            print(q.summary())
            dead = q.dlq()
            if dead:
                print("DLQ:")
                for d in dead:
                    print(f"  #{d['id']} {d['bot_id']}: {d['dead_reason']}")
        except Exception as e:
            print(f"QUEUE_ERROR {e}")
        return 0

    now = datetime.now()
    state = load_state()
    bots_cfg = load_config()

    # --enqueue-due: side-effect enqueue of due work orders (window-stable
    # keys). Called by sched_workorder after the dry-run print; stdout stays
    # empty so the monitor hash is unaffected. NO state mutation (no last_run).
    if args.enqueue_due:
        try:
            q = _get_queue()
            due = [b for b in bots if is_due(b, state, now, bots_cfg)]
            n = 0
            for b in due:
                if b.get("needs_approval"):
                    continue  # pending approvals are not executable work
                wo = work_order(b, force=False)
                key = _window_key(b, now)
                if q.enqueue(b["id"], wo, idem_key=key) != "dup":
                    n += 1
            print(f"ENQUEUED {n} (queue: {q.summary()})")
        except Exception as e:
            print(f"ENQUEUE_ERROR {e}")
        return 0

    # --queue-done / --queue-fail: agent-friendly queue lifecycle (resolve by
    # bot id, no numeric order ids needed).
    if args.queue_done:
        try:
            q = _get_queue()
            oid = q.complete_latest(args.queue_done)
            print(f"QUEUE_DONE {args.queue_done} order={oid}" if oid > 0
                  else f"QUEUE_DONE {args.queue_done} no-active-order")
        except Exception as e:
            print(f"QUEUE_DONE_ERROR {e}")
        return 0
    if args.queue_fail:
        try:
            q = _get_queue()
            oid = q.fail_latest(args.queue_fail[0], " ".join(args.queue_fail[1:]) or "failed")
            print(f"QUEUE_FAIL {args.queue_fail[0]} order={oid}" if oid > 0
                  else f"QUEUE_FAIL {args.queue_fail[0]} no-active-order")
        except Exception as e:
            print(f"QUEUE_FAIL_ERROR {e}")
        return 0

    # --mark-done: record TRUE completion time (the executing agent calls this
    # after a work order finishes, so last_run reflects execution, not print).
    if args.mark_done:
        known = {b["id"] for b in bots}
        missing = [i for i in args.mark_done if i not in known]
        if missing:
            print(f"NO_BOT {', '.join(missing)}")
            return 1
        for bid in args.mark_done:
            state.setdefault(bid, {})["last_run"] = now.isoformat()
        save_state(state)
        print("MARKED_DONE " + " ".join(args.mark_done))
        return 0

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
            # DENY-BY-DEFAULT (research-backed, 2026-08-14): a pending approval
            # that times out resolves to DENIED, never runs, and is audited.
            # Default 24h; per-bot override via approval_timeout_h.
            timeout_h = float(bot.get("approval_timeout_h", 24))
            prev = state.get(bot["id"], {}).get("pending_since")
            if prev and (now - datetime.fromisoformat(prev)).total_seconds() / 3600 >= timeout_h:
                state.setdefault(bot["id"], {})["last_run"] = now.isoformat()
                state[bot["id"]].pop("pending_since", None)
                _audit_approval(bot, "denied", f"approval timeout ({timeout_h}h)")
                print(f"APPROVAL_DENIED {bot['id']}: pending since {prev} — "
                      f"auto-denied after {timeout_h}h timeout (deny-by-default).")
                continue
            print(f"PENDING_APPROVAL {bot['id']}: {bot.get('name', bot['id'])}"
                  f" — {bot.get('approval_note', 'admin action requested')}."
                  f" Approve? (deliver this to the owner's phone, wait for reply)"
                  f" Auto-denies after {timeout_h:g}h.")
            if not args.dry_run:
                st = state.setdefault(bot["id"], {})
                st["last_run"] = now.isoformat()
                st["pending_since"] = st.get("pending_since", now.isoformat())
            continue
        # Enqueue the work order into the persistent queue (idempotent by
        # bot id + run timestamp) so the executing agent can claim it and
        # failures retry/dead-letter instead of vanishing.
        wo = work_order(bot, force=bool(args.force))
        if not args.dry_run:
            try:
                q = _get_queue()
                q.enqueue(bot["id"], wo, idem_key=f"{bot['id']}:{now.isoformat()}")
            except Exception:
                pass  # queue is an optimization — never block scheduling
        print(wo)
        if not args.dry_run:
            state.setdefault(bot["id"], {})["last_run"] = now.isoformat()
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
