"""Re-ingest specific vault notes that the bulk refresh missed or failed.

graph_refresh.py takes no arguments and skips nothing already ingested, so a
partial run cannot be resumed with it. This does the targeted follow-up: same
add_episode call, explicit file list, per-file timing, and a before/after node
count so you can see whether the note actually added knowledge.

Usage:
    python graph_repair.py FILE [FILE ...]
    python graph_repair.py --all-missing          # auto: failed + unprocessed
Env:
    GRAPHITI_LLM_TIMEOUT_S / GRAPHITI_DISABLE_THINKING / GRAPHITI_STRUCTURED_OUTPUT_MODE
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VAULT = Path(r"E:\Local\brain")

# ~2.5 chars/token for this model + ~18k tokens of fixed Graphiti extraction prompt.
# 40,000 chars ~= 16k note tokens ~= 34k total, comfortably inside a 65,536 window.
CHUNK_CHARS = 40000

# From the 2026-09-13 run: these failed (5, 13, 24, 25) and these were never reached
# because the 12 h budget stopped the run at 27/33.
KNOWN_BAD = [
    "notes/2026-09-12-current-stack.md",
    "notes/Ken-fork-project-2026-08-15.md",
    "notes/Tooling MOC.md",
    "notes/Two things are named Ken.md",
]
NEVER_REACHED = [
    "notes/Weather Arb MOC.md",   # already done at 27 -- kept out below
]


def counts() -> dict:
    from neo4j import GraphDatabase
    d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "graphitipass123"))
    with d.session() as s:
        out = {}
        for k, q in [
            ("episodes", "MATCH (e:Episodic) RETURN count(e) AS c"),
            ("entities", "MATCH (e:Entity) RETURN count(e) AS c"),
            ("live_edges", "MATCH ()-[r:RELATES_TO]->() WHERE r.invalid_at IS NULL RETURN count(r) AS c"),
        ]:
            out[k] = list(s.run(q))[0]["c"]
    d.close()
    return out


async def run(paths: list[Path], chunk: bool = False) -> tuple[int, int]:
    from graphiti_core.nodes import EpisodeType
    from assistant import graph as _graph

    g = _graph.build_client()
    ok = fail = 0
    try:
        for i, path in enumerate(paths, 1):
            body = path.read_text(encoding="utf-8", errors="replace")
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc)

            # Graphiti adds ~18k tokens of fixed extraction prompt, and this model's
            # tokeniser runs ~2.5 chars/token on prose. A note that would overflow the
            # loaded window is split at heading boundaries instead of being sent to die
            # with "request (N tokens) exceeds the available context size".
            pieces = split_note(body, CHUNK_CHARS) if chunk else [body]
            if len(pieces) > 1:
                print(f"  [{i}/{len(paths)}] {path.name}: {len(body)} ch -> "
                      f"{len(pieces)} chunk(s)", flush=True)

            before = counts()
            t0 = time.perf_counter()
            try:
                for j, piece in enumerate(pieces, 1):
                    name = path.stem if len(pieces) == 1 else f"{path.stem} [part {j} of {len(pieces)}]"
                    await g.add_episode(
                        name=name,
                        episode_body=piece,
                        source_description=f"Obsidian vault note: {path.relative_to(VAULT)}",
                        reference_time=mtime,
                        source=EpisodeType.text,
                    )
                after = counts()
                ok += 1
                print(f"  [{i}/{len(paths)}] OK   {path.name} ({len(body)} ch, "
                      f"{time.perf_counter() - t0:.0f}s)  "
                      f"entities {before['entities']}->{after['entities']}  "
                      f"live_edges {before['live_edges']}->{after['live_edges']}", flush=True)
            except Exception as exc:  # noqa: BLE001
                fail += 1
                print(f"  [{i}/{len(paths)}] FAIL {path.name} -> {type(exc).__name__}: "
                      f"{str(exc)[:180]}", flush=True)
    finally:
        await g.close()
    return ok, fail


def split_note(text: str, limit: int) -> list[str]:
    """Split a long note at markdown heading boundaries, then blank lines, then hard cuts.

    Heading boundaries keep each chunk semantically whole, which matters because
    Graphiti extracts entities/relations per episode -- an arbitrary mid-sentence cut
    produces dangling half-relations.
    """
    if len(text) <= limit:
        return [text]

    import re as _re

    def hard_split(s: str) -> list[str]:
        return [s[k:k + limit] for k in range(0, len(s), limit)] if s.strip() else []

    parts: list[str] = []
    cur: list[str] = []
    size = 0
    for block in _re.split(r"(?m)^(?=#{1,3} )", text):
        if len(block) > limit:
            if cur:
                parts.append("".join(cur))
                cur, size = [], 0
            parts.extend(hard_split(block))
            continue
        if size + len(block) > limit and cur:
            parts.append("".join(cur))
            cur, size = [], 0
        cur.append(block)
        size += len(block)
    if cur:
        parts.append("".join(cur))
    return [p for p in parts if p.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="vault-relative or absolute note paths")
    ap.add_argument("--chunk", action="store_true",
                    help=f"split notes over {CHUNK_CHARS} chars into heading-aligned episodes")
    args = ap.parse_args()

    if not args.files:
        print("no files given; pass paths or use --help")
        return 2

    paths = []
    for f in args.files:
        p = Path(f)
        if not p.is_absolute():
            cand = VAULT / f
            if not cand.exists():
                # tolerate a bare filename: notes/ and projects/ are both ingest roots
                hits = [h for h in VAULT.rglob(Path(f).name) if not h.name.startswith(".")]
                if not hits:
                    print(f"  MISSING: {f}")
                    return 2
                cand = hits[0]
            p = cand
        if not p.exists():
            print(f"  MISSING: {p}")
            return 2
        paths.append(p)

    print(f"graph repair: {len(paths)} note(s)")
    print(f"  before: {counts()}")
    print(f"  timeout={__import__('os').environ.get('GRAPHITI_LLM_TIMEOUT_S', '1800')}s "
          f"thinking_off={__import__('os').environ.get('GRAPHITI_DISABLE_THINKING', '1')} "
          f"mode={__import__('os').environ.get('GRAPHITI_STRUCTURED_OUTPUT_MODE', 'json_schema')}")
    print()
    t0 = time.perf_counter()
    ok, fail = asyncio.run(run(paths, args.chunk))
    print()
    print(f"  after : {counts()}")
    print(f"  ingested={ok} failed={fail} in {time.perf_counter() - t0:.0f}s")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
