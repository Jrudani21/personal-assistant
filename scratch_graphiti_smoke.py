"""One-note extraction smoke test: does a local Ollama model produce a real graph?

Not part of the app. Run once, inspect output, delete or promote based on result.
"""
import asyncio
from datetime import datetime, timezone

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
EXTRACTION_MODEL = "deepseek-r1:7b"
EMBED_MODEL = "nomic-embed-text"

NOTE_TITLE = "Overdispersion breaks the Poisson variance assumption"
NOTE_PATH = r"E:\Claude\brain\notes\Overdispersion breaks the Poisson variance assumption.md"


async def main():
    with open(NOTE_PATH, encoding="utf-8") as f:
        note_body = f.read()

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

    print(f"ingesting episode: {NOTE_TITLE}")
    await graphiti.add_episode(
        name=NOTE_TITLE,
        episode_body=note_body,
        source_description="Obsidian vault note",
        reference_time=datetime.now(timezone.utc),
        source=EpisodeType.text,
    )

    print("\n--- querying graph for 'Poisson' ---")
    results = await graphiti.search("Poisson overdispersion")
    if not results:
        print("EMPTY RESULT — extraction likely failed.")
    for r in results:
        print(f"- {r.fact}")

    await graphiti.close()


if __name__ == "__main__":
    asyncio.run(main())
