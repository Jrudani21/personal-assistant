#!/usr/bin/env python
"""Verify bot_runner price-window + deferral logic."""
import sys
sys.path.insert(0, r"E:/Local/projects/personal-assistant/qa")
from datetime import datetime
import bot_runner as br

tests = [
    (0,  "neutral"),     # 00:00 -> cheap window (23:00-01:00 neutral), not peak
    (1,  "expensive"),   # 01:00 peak start
    (3,  "expensive"),   # 03:30 deep peak
    (5,  "expensive"),   # 05:00 peak end (inclusive)
    (6,  "neutral"),     # 06:00 after peak
    (11, "discount"),    # 11:30 discount start
    (15, "discount"),    # mid discount
    (19, "discount"),    # 19:30 discount end
    (20, "expensive"),   # 20:00 peak start
    (22, "expensive"),   # deep peak
    (23, "expensive"),   # 23:00 peak end
]
ok = True
for h, expect in tests:
    got = br.price_window(datetime(2026, 8, 12, h, 0))
    mark = "OK " if got == expect else "FAIL"
    if got != expect: ok = False
    print(f"{mark} {h:02d}:00 -> {got} (expect {expect})")

# Deferral: a research bot due at 03:00 must NOT run; at 06:00 it should.
state = {}
bot = {"id": "career-ops-research", "type": "research", "schedule": "daily:03:20", "enabled": True}
d1 = datetime(2026, 8, 12, 3, 30)   # peak
d2 = datetime(2026, 8, 12, 6, 0)    # neutral
print("defer at 03:30 (expect False):", br.is_due(bot, state, d1))
print("run at 06:00 (expect True):", br.is_due(bot, state, d2))
print("ALL PASS" if ok else "SOME FAILED")
