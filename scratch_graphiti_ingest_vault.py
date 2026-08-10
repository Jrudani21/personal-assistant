"""Full vault ingestion: all Obsidian notes into Graphiti graph.

Not part of the app. Run once after smoke test passed. Config confirmed
working: qwen3-coder:30b + json_schema structured output mode.
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EpisodeType

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "graphitipass123"

OLLAMA_BASE_URL = "http://localhost:11434/v1"
EXTRACTION_MODEL = "qwen3-coder:30b"
EMBED_MODEL = "nomic-embed-text"

VAULT_ROOT = Path(r"E:\Claude\brain")
EXCLUDE_DIRS = {"templates"}


def collect_notes():
    notes = []
    for path in sorted(VAULT_ROOT.rglob("*.md")):
        if EXCLUDE_DIRS & set(path.relative_to(VAULT_ROOT).parts):
            continue
        notes.append(path)
    return notes


async def main():
    notes = collect_notes()
    print(f"found {len(notes)} notes to ingest")
    for p in notes:
        print(f"  - {p.relative_to(VAULT_ROOT)}")

    llm_config = LLMConfig(
        api_key="ollama",
        model=EXTRACTION_MODEL,
        small_model=EXTRACTION_MODEL,
        base_url=OLLAMA_BASE_URL,
    )
    llm_client = OpenAIGenericClient(config=llm_config, structured_output_mode="json_schema")

    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="ollama",
            embedding_model=EMBED_MODEL,
            embedding_dim=768,
            base_url=OLLAMA_BASE_URL,
        )
    )

    cross_encoder = OpenAIRerankerClient(config=llm_config)

    graphiti = Graphiti(
        NEO4J_URI,
        NEO4J_USER,
        NEO4J_PASSWORD,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )

    print("building indices...")
    await graphiti.build_indices_and_constraints()

    for i, path in enumerate(notes, 1):
        body = path.read_text(encoding="utf-8")
        name = path.stem
        print(f"[{i}/{len(notes)}] ingesting: {name}")
        try:
            await graphiti.add_episode(
                name=name,
                episode_body=body,
                source_description=f"Obsidian vault note ({path.relative_to(VAULT_ROOT)})",
                reference_time=datetime.now(timezone.utc),
                source=EpisodeType.text,
            )
        except Exception as e:
            print(f"  FAILED: {name}: {e}")

    print("\n--- sanity query: 'Poisson' ---")
    results = await graphiti.search("Poisson overdispersion")
    for r in results:
        print(f"- {r.fact}")

    print("\n--- sanity query: 'Graphiti' ---")
    results = await graphiti.search("Graphiti second brain knowledge graph")
    for r in results:
        print(f"- {r.fact}")

    await graphiti.close()


if __name__ == "__main__":
    asyncio.run(main())
