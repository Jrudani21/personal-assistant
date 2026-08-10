"""MCP server exposing graph_search over the Graphiti second-brain knowledge graph."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

from assistant import graph

mcp = FastMCP("graphiti-second-brain")


@mcp.tool()
def graph_search(query: str, top_k: int = 5) -> str:
    """Search the Graphiti knowledge graph (built from the Obsidian vault) for facts and relationships matching a query."""
    return graph.graph_search(query, top_k)


if __name__ == "__main__":
    mcp.run()
