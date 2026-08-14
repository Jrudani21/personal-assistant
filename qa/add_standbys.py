#!/usr/bin/env python
"""Add standby bots to the fleet config (failover spares)."""
import json
from pathlib import Path

P = Path(r"E:/Local/projects/personal-assistant/data/bots.json")
d = json.loads(P.read_text(encoding="utf-8"))
bots = d["bots"]
by_id = {b["id"]: b for b in bots}

# Standby bots: same runner, standby_for = primary, lower budget, disabled-ish
# (enabled but only run when primary is stale, per is_due logic).
standbys = [
    {
        "id": "qa-tester-standby",
        "type": "qa",
        "name": "QA Tester (standby)",
        "fleet": "qa",
        "schedule": "hourly",
        "runner": "qa/qa_runner.py",
        "target": "sandbox:8766",
        "model": None,
        "enabled": True,
        "standby_for": "qa-tester",
        "capabilities": ["testing", "bug-reporting"],
        "budget": {"max_runs_per_day": 24, "max_cost_est": 0},
        "notes": "Standby for qa-tester — only runs if the primary misses its window."
    },
    {
        "id": "health-watch-standby",
        "type": "health",
        "name": "Health Watch (standby)",
        "fleet": "ops",
        "schedule": "daily:05:30",
        "runner": "qa/provider_health.py",
        "target": "all",
        "model": None,
        "enabled": True,
        "standby_for": "health-watch",
        "capabilities": ["health-checks"],
        "budget": {"max_runs_per_day": 3, "max_cost_est": 0},
        "notes": "Standby for health-watch — takes over if the primary fails to run."
    },
    {
        "id": "career-ops-standby",
        "type": "research",
        "name": "Career Ops (standby)",
        "fleet": "research",
        "schedule": "daily:03:50",
        "topic": "Resume and career-ops pipeline research (standby rerun): ATS-friendly resume formats 2026, job application tracking, keyword optimization, cover letter generation, interview prep, career-ops pipeline structure.",
        "depth": "medium",
        "subagents": 2,
        "model": "deepseek-v4-flash",
        "sources": ["web_search", "web_extract"],
        "output": "data/research/career-ops/",
        "enabled": True,
        "standby_for": "career-ops",
        "capabilities": ["career-ops"],
        "budget": {"max_runs_per_day": 1, "max_cost_est": 8000},
        "notes": "Standby for career-ops — only runs if the primary missed its window."
    },
]

# Add standbys not already present, register in their fleet's members
for sb in standbys:
    if sb["id"] not in by_id:
        bots.append(sb)
        for f in d["fleets"]:
            if f["id"] == sb["fleet"] and sb["id"] not in f["members"]:
                f["members"].append(sb["id"])

P.write_text(json.dumps(d, indent=2), encoding="utf-8")
print(f"bots now: {len(bots)}")
for sb in standbys:
    print(f"  + {sb['id']} (standby_for={sb['standby_for']})")
