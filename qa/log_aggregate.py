#!/usr/bin/env python
"""EOD log aggregator — collect EVERYTHING into ONE bounded bundle.

All fleet logs + state get pulled into a single text bundle with per-source
caps, so ONE LLM API request can process the whole day. Bounded by design:
total bundle stays ~25-40KB (~8-12K tokens) — one cheap DeepSeek call.

Sources (each capped):
1. fleet.jsonl (unified structured log) — last 400 events
2. ken_service.log — last 60 lines
3. agent logs (data/agents/*.log) — last 25 lines each
4. cost_ledger.jsonl — today's entries
5. work queue summary + DLQ
6. event_bus stats
7. watchdog_alerts.jsonl — today's alerts
8. distill_state — notes distilled count
9. cron jobs (hermes jobs.json) — today's runs + status
10. fleet_report.md — issues section

Output: data/logs/eod_bundle.txt (stable format, used by eod_analysis.py).
Prints the bundle path + byte size.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PA = Path(r"E:\Local\projects\personal-assistant")
AGENTS = Path(r"E:\Local\projects\500-AI-Agents-Projects")
OUT = PA / "data" / "logs" / "eod_bundle.txt"


def _tail(path: Path, n: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(missing)"


def _today_ledger() -> str:
    try:
        ledger = AGENTS / "data" / "cost_ledger.jsonl"
        tot = {"calls": 0, "pt": 0, "ct": 0, "cht": 0, "cost": 0.0}
        rows = []
        for line in ledger.read_text(encoding="utf-8").strip().splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("ts", "").startswith(__import__("datetime").date.today().isoformat()):
                tot["calls"] += 1
                tot["pt"] += e.get("prompt_tokens", 0)
                tot["ct"] += e.get("completion_tokens", 0)
                tot["cht"] += e.get("cache_hit_tokens", 0)
                tot["cost"] += e.get("est_cost_usd", 0)
                rows.append(e)
        return f"totals: {json.dumps(tot)}\nlast entries:\n" + "\n".join(
            json.dumps(r) for r in rows[-10:]
        )
    except Exception:
        return "(ledger missing)"


def _queue() -> str:
    try:
        r = subprocess.run(
            [sys.executable, str(PA / "qa" / "bot_runner.py"), "--queue"],
            capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")},
        )
        return (r.stdout or r.stderr or "").strip()
    except Exception:
        return "(queue check failed)"


def _cron_runs() -> str:
    try:
        jobs_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "cron" / "jobs.json"
        d = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs = d if isinstance(d, list) else d.get("jobs", [])
        today = __import__("datetime").date.today().isoformat()
        out = []
        for j in jobs:
            lr = j.get("last_run_at", "") or ""
            if lr.startswith(today):
                out.append(f"{j.get('name','?')}: {lr[11:16]} status={j.get('last_status')}")
        return "\n".join(out) or "(no cron runs today)"
    except Exception:
        return "(cron info unavailable)"


def build() -> Path:
    sections = []
    sections.append(("UNIFIED FLEET LOG (fleet.jsonl)", _tail(PA / "data" / "logs" / "fleet.jsonl", 400)))
    sections.append(("KEN SERVICE LOG", _tail(PA / "data" / "ken_service.log", 60)))
    agent_logs = sorted((PA / "data" / "agents").glob("*.log"))
    agent_txt = "\n\n".join(
        f"--- {p.name} ---\n{_tail(p, 25)}" for p in agent_logs
    ) or "(no agent logs)"
    sections.append(("AGENT LOGS", agent_txt))
    sections.append(("COST LEDGER (today)", _today_ledger()))
    sections.append(("WORK QUEUE + DLQ", _queue()))
    try:
        sys.path.insert(0, str(PA / "qa"))
        from event_bus import stats as eb_stats
        sections.append(("EVENT BUS", json.dumps(eb_stats())))
    except Exception:
        sections.append(("EVENT BUS", "(unavailable)"))
    sections.append(("WATCHDOG ALERTS", _tail(PA / "data" / "watchdog_alerts.jsonl", 20)))
    try:
        st = json.loads((PA / "data" / "distill_state.json").read_text(encoding="utf-8"))
        sections.append(("MEMORY DISTILL", f"{len(st)} notes distilled"))
    except Exception:
        sections.append(("MEMORY DISTILL", "(unavailable)"))
    sections.append(("CRON RUNS (today)", _cron_runs()))
    sections.append(("FLEET REPORT ISSUES", _tail(PA / "data" / "fleet_report.md", 25)))

    parts = []
    for title, body in sections:
        parts.append(f"===== {title} =====")
        parts.append(body.strip() or "(empty)")
        parts.append("")
    bundle = "\n".join(parts).strip()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(bundle, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    p = build()
    print(f"BUNDLE {p} ({p.stat().st_size} bytes)")
