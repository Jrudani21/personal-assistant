"""One-shot: Ingest all Obsidian vault notes into Graphiti knowledge graph.
Replaces the old 11-note ingestion with the current 17 notes.
Usage: python ingest_vault.py
"""
import asyncio, datetime, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assistant import vault
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EpisodeType

NEO4J_URI = "bolt://localhost:7687"
NEO4J_PASSWORD = "graphitipass123"
OLLAMA_BASE = "http://localhost:11434/v1"
EXTRACTION_MODEL = "deepseek-r1-16k:7b"
EMBED_MODEL = "nomic-embed-text"

def build_graphiti():
    llm_config = LLMConfig(api_key="ollama", model=EXTRACTION_MODEL, small_model=EXTRACTION_MODEL, base_url=OLLAMA_BASE)
    llm = OpenAIGenericClient(config=llm_config, structured_output_mode="json_schema")
    embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(api_key="ollama", embedding_model=EMBED_MODEL, embedding_dim=768, base_url=OLLAMA_BASE))
    reranker = OpenAIRerankerClient(config=llm_config)
    return Graphiti(NEO4J_URI, "neo4j", NEO4J_PASSWORD, llm_client=llm, embedder=embedder, cross_encoder=reranker)

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
