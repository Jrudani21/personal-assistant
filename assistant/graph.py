"""Graph search over the second-brain Graphiti knowledge graph (Neo4j + the local model)."""
import asyncio

from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

from . import config as _config
from . import local_llm

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "graphitipass123"

# Graphiti wants an OpenAI-style endpoint, so it rides the shared local transport
# instead of hardcoding Ollama's :11434 and Ollama-only model tags (neither of
# which exists on this machine any more).
EMBED_MODEL_ALIAS = "nomic-embed-text"   # resolved to whatever the backend serves


def build_client() -> Graphiti:
    """Build a Graphiti client on the same local transport as the rest of the app.

    Override the extraction model with the `graph_extraction_model` config key —
    graph extraction wants strict JSON, which a small thinking model is not
    necessarily good at.
    """
    base_url = local_llm.lmstudio_base()
    model = str(_config.get("graph_extraction_model", "") or "") or local_llm.chat_model()
    llm_config = LLMConfig(
        api_key=local_llm.api_key(),
        model=model,
        small_model=model,
        base_url=base_url,
    )
    llm_client = OpenAIGenericClient(config=llm_config, structured_output_mode="json_schema")
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=local_llm.api_key(),
            embedding_model=local_llm.resolve_model(EMBED_MODEL_ALIAS),
            embedding_dim=768,
            base_url=base_url,
        )
    )
    cross_encoder = OpenAIRerankerClient(config=llm_config)
    return Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm_client, embedder=embedder, cross_encoder=cross_encoder,
    )


async def _search(query: str, top_k: int) -> list[str]:
    graphiti = build_client()
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
