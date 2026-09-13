"""Graph search over the second-brain Graphiti knowledge graph (Neo4j + the local model)."""
import asyncio
import os

from openai import AsyncOpenAI
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

# One local GPU serves exactly one request at a time: LM Studio must run
# `--parallel 1` or the 65536-token window is split across slots and the big
# vault notes stop fitting. Graphiti's own default is 20 concurrent LLM calls
# (graphiti_core/helpers.py: SEMAPHORE_LIMIT), so on a one-slot server 19 of them
# just sit in the server queue while the OpenAI SDK's 600 s read timeout expires
# on requests that were never being worked on -- and every expiry discards a
# full prompt prefill and starts over. 28 of those cost ~4.7 h of a 5.5 h run.
# Serialize client-side instead, and give a genuinely long generation room to
# finish rather than killing it mid-flight.
LLM_HTTP_TIMEOUT_S = float(os.environ.get("GRAPHITI_LLM_TIMEOUT_S", "1800"))
MAX_COROUTINES = int(os.environ.get("GRAPHITI_MAX_COROUTINES", "1"))

# "json_schema" asks LM Studio for strict grammar-constrained decoding. That is the
# right default, but when a small model degenerates it loops inside the grammar
# until max_tokens, which is slow and yields sentence-long relation names like
# ..._CAPS_DAILY_MAX_ON_HOT_DAYS. "json_object" is a looser fallback worth having
# per-run for exactly those notes.
STRUCTURED_OUTPUT_MODE = os.environ.get("GRAPHITI_STRUCTURED_OUTPUT_MODE", "json_schema")

# Measured 2026-09-13 from LM Studio's own server log: on graph-extraction calls
# qwen3.5-9b emits ~98% thinking tokens. A call needing ~250 tokens of JSON burned
# 6,787 completion tokens, of which 6,537 were chain-of-thought (26x waste), and the
# model rambles further on each retry. Thinking buys nothing here -- the task is
# schema-constrained extraction from text that is already in the prompt -- so it is
# off by default for this client.
DISABLE_THINKING = os.environ.get("GRAPHITI_DISABLE_THINKING", "1") == "1"


class _NoThinkCompletions:
    """chat.completions proxy that forces reasoning_effort=none via extra_body."""

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def create(self, **kwargs):
        extra = dict(kwargs.get("extra_body") or {})
        # Measured, one variant at a time, against LM Studio 5.x + qwen3.5-9b:
        # chat_template_kwargs {enable_thinking|thinking: false} AND a "/no_think"
        # prompt suffix are all NO-OPS here (150/150 tokens still reasoning).
        # reasoning_effort="none" is the only form that actually disables it
        # (reasoning_tokens 150 -> 0, and content finally appears).
        extra["reasoning_effort"] = "none"
        kwargs["extra_body"] = extra
        return await self._inner.create(**kwargs)


class _NoThinkChat:
    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def completions(self):
        return _NoThinkCompletions(self._inner.completions)


class _NoThinkClient:
    """Transparent AsyncOpenAI proxy.

    OpenAIGenericClient._generate_response calls `self.client.chat.completions.create`
    directly, so wrapping the client it is handed is enough -- no monkeypatching.
    LM Studio reads chat_template_kwargs out of the request body.
    """

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def chat(self):
        return _NoThinkChat(self._inner.chat)


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
    http_client = AsyncOpenAI(
        api_key=local_llm.api_key(),
        base_url=base_url,
        timeout=LLM_HTTP_TIMEOUT_S,
        max_retries=0,   # a retry costs another full prefill; fail loudly instead
    )
    if DISABLE_THINKING:
        http_client = _NoThinkClient(http_client)
    llm_client = OpenAIGenericClient(
        config=llm_config,
        client=http_client,
        structured_output_mode=STRUCTURED_OUTPUT_MODE,
    )
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
        max_coroutines=MAX_COROUTINES,
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
