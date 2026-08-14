"""MCP server exposing graph_search over the Graphiti second-brain knowledge graph."""
import os
import sys
from pathlib import Path

# Hermes venv leak guard: if PYTHONPATH injects the Hermes (py3.11) venv site-packages,
# Python312 cannot load its pydantic_core (ABI mismatch). Scrub any such entries.
_HERMES_MARKERS = ("hermes-agent", "\\hermes\\", "/hermes/")
_clean = []
for _p in sys.path:
    if _p and any(m in _p.lower() for m in _HERMES_MARKERS):
        continue
    _clean.append(_p)
sys.path[:] = _clean
os.environ.pop("PYTHONPATH", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

from assistant import graph

mcp = FastMCP("graphiti-second-brain")


@mcp.tool()
async def graph_search(query: str, top_k: int = 5) -> str:
    """Search the Graphiti knowledge graph (built from the Obsidian vault) for facts and relationships matching a query."""
    return await graph.graph_search_async(query, top_k)


if __name__ == "__main__":
    mcp.run()
