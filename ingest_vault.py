"""One-shot: Ingest all Obsidian vault notes into Graphiti knowledge graph.
Replaces the old 11-note ingestion with the current 17 notes.
Usage: python ingest_vault.py
"""
import asyncio, datetime, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assistant import graph as _graph
from assistant import vault
from graphiti_core.nodes import EpisodeType


def build_graphiti():
    """Reuse assistant.graph's client: one place owns the local endpoint, the model
    ids and the credentials, so this script and the search path cannot drift apart
    (they had: both hardcoded Ollama's :11434 and Ollama-only model tags)."""
    return _graph.build_client()

async def main():
    notes = vault.list_notes()
    print(f"Vault: {len(notes)} notes to ingest")
    
    g = build_graphiti()
    ok = 0
    fail = 0
    try:
        for n in notes:
            body = vault.read_note(n['rel'])
            if not body:
                print(f"  SKIP: {n['rel']} (unreadable)")
                fail += 1
                continue
            name = n['name']
            ref_time = n.get('mtime', datetime.datetime.now())
            try:
                await g.add_episode(
                    name=name,
                    episode_body=body,
                    source_description=f"Obsidian vault note: {n['rel']}",
                    reference_time=ref_time,
                    source=EpisodeType.text,
                )
                ok += 1
                print(f"  OK: {name} ({len(body)} chars)")
            except Exception as e:
                fail += 1
                print(f"  FAIL: {name} -> {e}")
    finally:
        await g.close()
    
    print(f"\nDone: {ok} ingested, {fail} failed, {len(notes)} total")

if __name__ == "__main__":
    asyncio.run(main())
