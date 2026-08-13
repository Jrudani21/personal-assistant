#!/usr/bin/env python
"""Logic check: standby failover, price windows, budgets, coordinators."""
import sys
sys.path.insert(0, r"E:/Local/projects/personal-assistant/qa")
from datetime import datetime, timedelta
import bot_runner as br
import json

cfg = json.loads(open(r"E:/Local/projects/personal-assistant/data/bots.json", encoding="utf-8").read())
bots = {b["id"]: b for b in cfg["bots"]}

now = datetime(2026, 8, 13, 10, 0)  # neutral price
failures = []

def check(name, got, expect):
    ok = got == expect
    failures.append((name, got, expect, ok))
    print(f"{'✅' if ok else '❌'} {name}: got={got} expect={expect}")

# --- Standby logic ---
# 1. Primary healthy (ran 1h ago) -> spare idle
st1 = {"qa-tester": {"last_run": (now - timedelta(hours=1)).isoformat()}}
check("standby idle when primary healthy",
      br.is_due(bots["qa-tester-standby"], st1, now, cfg), False)

# 2. Primary stale (ran 10h ago, hourly schedule -> gap 3h) -> spare runs
st2 = {"qa-tester": {"last_run": (now - timedelta(hours=10)).isoformat()}}
check("standby runs when primary stale",
      br.is_due(bots["qa-tester-standby"], st2, now, cfg), True)

# 3. Primary never ran -> spare runs
check("standby runs when primary never ran",
      br.is_due(bots["qa-tester-standby"], {}, now, cfg), True)

# 4. Primary itself still due (healthy schedule)
check("primary still due normally",
      br.is_due(bots["qa-tester"], st1, now, cfg), True)

# --- Price window ---
check("price 10:00 neutral", br.price_window(now), "neutral")
check("price 03:00 expensive", br.price_window(datetime(2026,8,13,3,0)), "expensive")
check("price 15:00 discount", br.price_window(datetime(2026,8,13,15,0)), "discount")

# Research defers in peak
check("research defers at peak", br.is_due(bots["career-ops"], {}, datetime(2026,8,13,3,0), cfg), False)
# career-ops schedule = daily:18:20 (moved off the 02:00-05:00 dead window)
check("research runs when due (off-peak)", br.is_due(bots["career-ops"], {}, datetime(2026,8,13,18,30), cfg), True)

# --- Coordinator approval ---
ops_boss = bots["ops-boss"]
check("ops-boss can_approve", ops_boss.get("can_approve"), True)
qa_lead = bots["qa-lead"]
check("qa-lead can_approve", qa_lead.get("can_approve"), True)

# --- Budget present ---
for bid in ["qa-tester", "health-watch", "career-ops"]:
    check(f"{bid} has budget", "budget" in bots[bid], True)

print()
print("TOTAL:", f"{sum(1 for f in failures if f[3])}/{len(failures)} passed")
sys.exit(0 if all(f[3] for f in failures) else 1)
