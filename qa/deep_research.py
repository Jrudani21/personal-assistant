#!/usr/bin/env python
"""KEN deep-research executor (agent side).

Reads a research bot's config from data/bots.json and prints a SELF-CONTAINED
research brief that an agent (Hermes) can execute via delegate_task following
the Anthropic orchestrator-worker pattern. The brief includes the subtask
spec template so subagents don't duplicate work.

The agent running this reads the brief, spawns N parallel subagents (each
writes findings to data/research/<bot>/subagent-N.md), then synthesizes.

Usage:
  python qa/deep_research.py <bot_id> --topic "..."    # print brief
  python qa/deep_research.py <bot_id> --topic "..." --out brief.txt
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS_FILE = ROOT / "data" / "bots.json"


def load_bot(bot_id: str) -> dict | None:
    try:
        bots = json.loads(BOTS_FILE.read_text(encoding="utf-8")).get("bots", [])
    except Exception:
        bots = []
    return next((b for b in bots if b["id"] == bot_id), None)


def build_brief(bot: dict, topic: str) -> str:
    n = int(bot.get("subagents", 3))
    depth = bot.get("depth", "medium")
    sources = ", ".join(bot.get("sources", ["web_search"]))
    outdir = bot.get("output", "data/research/") + bot["id"]
    model = bot.get("model", "deepseek-v4-flash")

    # Subtask-spec template (Anthropic: objective + output + tools + boundary).
    # Built with .format() so the placeholders resolve to real values.
    spec = (
        'Each subagent gets this spec (unique angle assigned by the lead):\n'
        '  OBJECTIVE: research exactly ONE angle of: "{topic}"\n'
        '  OUTPUT: write findings to {outdir}/subagent-<n>.md (markdown, bullet facts,\n'
        '          with source URLs per claim). Keep under 4000 chars.\n'
        '  TOOLS: {sources}. Ground everything; never invent URLs.\n'
        '  BOUNDARY: do NOT research other angles (the other subagents own them).\n'
        '  RETURN: the file path + a 3-line summary only.'
    ).format(topic=topic, outdir=outdir, sources=sources)
    subtask_spec = spec

    return f"""# DEEP RESEARCH BRIEF — {bot['id']}
topic: {topic}
depth: {depth}   model: {model}   subagents: {n}
sources: {sources}   output: {outdir}

## Execute (agent instructions)
1. MKDIR {outdir} (ensure it exists).
2. LEAD-DECOMPOSE the topic into {n} NON-OVERLAPPING angles (e.g. history,
   current-state, data/numbers, controversies, future, sources).
3. SPAWN {n} parallel subagents via delegate_task, each with the spec below
   (angle filled in). All write to {outdir}/subagent-1.md .. subagent-{n}.md.
4. SYNTHESIZE: read all files, write {outdir}/report.md — structured,
   with inline [n] citations mapped to a Sources section (URLs).
5. POST: link the report in the daily brain note (E:/Local/brain/daily/).

## Subtask spec (per subagent)
{subtask_spec}

## Token budget
- This is HIGH-VALUE research: multi-agent spend is justified (Anthropic:
  ~15x chat tokens). Do it once, do it well.
- No retry loops. If a subagent returns junk, note it and continue.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bot_id")
    ap.add_argument("--topic", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    bot = load_bot(a.bot_id)
    if not bot:
        print(f"NO_BOT {a.bot_id} (check data/bots.json)")
        return 1
    if bot.get("type") != "research":
        print(f"BOT {a.bot_id} is type={bot['type']}, not research")
        return 1

    topic = a.topic or bot.get("topic", "")
    if not topic:
        print(f"BOT {a.bot_id} has no topic; pass --topic")
        return 1

    brief = build_brief(bot, topic)
    if a.out:
        Path(a.out).write_text(brief, encoding="utf-8")
        print(f"brief written to {a.out}")
    else:
        print(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
