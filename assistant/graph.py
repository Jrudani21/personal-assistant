"""Graph search over the second-brain Graphiti knowledge graph (Neo4j + Ollama)."""
import asyncio

from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "graphitipass123"

OLLAMA_BASE_URL = "http://localhost:11434/v1"
EXTRACTION_MODEL = "deepseek-r1-16k:7b"  # deepseek-r1:7b w/ num_ctx=16384 (default 4096 too small for extraction prompts)
EMBED_MODEL = "nomic-embed-text"


def _build_client() -> Graphiti:
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
    return Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm_client, embedder=embedder, cross_encoder=cross_encoder,
    )


async def _search(query: str, top_k: int) -> list[str]:
    graphiti = _build_client()
    try:
        results = await graphiti.search(query)
        return [r.fact for r in results[:top_k]]
    finally:
        await graphiti.close()


def _format(facts: list[str]) -> str:
    if not facts:
        return "No graph results found."
    return "\n".join(f"- {f}" for f in facts)


async def graph_search_async(query: str, top_k: int = 5) -> str:
    try:
        facts = await _search(query, top_k)
        return _format(facts)
    except Exception as e:
        return f"Graph search error: {e}"


def graph_search(query: str, top_k: int = 5) -> str:
    try:
        return asyncio.run(graph_search_async(query, top_k))
    except Exception as e:
        return f"Graph search error: {e}"
