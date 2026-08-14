#!/usr/bin/env python
"""Cache hit-rate report for the KEN fleet (L1 exact / L2 semantic / L3 prefix).

Reads the shared cache stats + cost ledger (agents/_shared) and prints a
compact weekly summary. Wired into the Monday morning cron.

Usage:
  python qa/cache_hit_report.py [--days 7]

Output (stdout, markdown):
  - L3 provider prefix hit rate over the window (prompt vs cache-hit tokens)
  - L1 exact / L2 semantic hit counters
  - call volume + est cost for the window
Never raises: empty/absent ledger -> zeros.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = Path(r"E:\Local\projects\500-AI-Agents-Projects\agents")
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

from _shared import cache, cost  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    try:
        rate = cost.cache_hit_rate(args.days)
    except Exception:
        rate = {"prompt_tokens": 0, "cache_hit_tokens": 0, "hit_rate": 0.0}
    try:
        st = cache.stats()
    except Exception:
        st = {}

    pt = rate["prompt_tokens"]
    cht = rate["cache_hit_tokens"]
    hit = rate["hit_rate"]
    pct = f"{hit * 100:.1f}%"

    # est cost for the window (ledger entries)
    est = 0.0
    try:
        ledger = cost.LEDGER
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                est += float(e.get("est_cost_usd", 0) or 0)
    except Exception:
        pass

    lines = [
        f"# Cache Report (last {args.days}d)",
        f"- **L3 prefix hit rate**: {pct} ({cht:,}/{pt:,} prompt tokens from cache)",
        f"- **L1 exact hits**: {st.get('exact_hits', 0)}",
        f"- **L2 semantic hits**: {st.get('semantic_hits', 0)}",
        f"- **Billed calls (ledger)**: est ${est:.4f}",
        "",
    ]
    if hit >= 0.6:
        lines.append("Target: >60% hit on stable-prompt bots — ON TRACK ✓")
    elif pt == 0:
        lines.append("No billed calls this window — nothing to measure yet.")
    else:
        lines.append("Below 60% — check for dynamic content leaking into the system prefix "
                     "(prompts.py STABLE_SYSTEMS must stay byte-identical).")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
