"""CrewAI Tool wrappers around assistant/tools.py, so crew agents can call
the same web_search/etc. functions the chat assistant already uses -- one
implementation, two callers. Exception: run_python runs its own isolated,
one-shot subprocess rather than assistant.tools.run_python's shared
persistent session, so crew code can't collide with the user's live chat
variables (or get wiped by the crew's own restart-on-timeout/crash)."""
import subprocess
import sys
from pathlib import Path

from crewai.tools import tool

from . import rag
from . import tools as _t

_WORKSPACE = Path(__file__).resolve().parent.parent / "data" / "workspace"


@tool("web_search")
def web_search_tool(query: str, max_results: int = 5) -> str:
    """Search the web for current information not available locally (news, facts, prices, recent events)."""
    return _t.web_search(query, max_results)


@tool("wikipedia_summary")
def wikipedia_summary_tool(topic: str) -> str:
    """Get a short Wikipedia summary of a topic, person, or place."""
    return _t.wikipedia_summary(topic)


@tool("read_file")
def read_file_tool(path: str) -> str:
    """Read a text file from the local workspace folder (data/workspace/), given a relative path."""
    return _t.read_file(path)


@tool("list_files")
def list_files_tool(subpath: str = "") -> str:
    """List files/folders in the local workspace (data/workspace/), or a subfolder of it."""
    return _t.list_files(subpath)


@tool("search_documents")
def search_documents_tool(query: str) -> str:
    """Search the user's previously uploaded documents (PDF/txt/md) for relevant passages."""
    return rag.search_documents(query)


@tool("calculator")
def calculator_tool(expression: str) -> str:
    """Evaluate a numeric arithmetic expression, e.g. '12 * (3 + 4)'."""
    return _t.calculator(expression)


@tool("run_python")
def run_python_tool(code: str) -> str:
    """Run a Python snippet (e.g. pandas/numpy) for real calculations or stats, in an isolated one-shot process (no shared state with the chat's run_python tool or across calls). Use print() to return output."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_WORKSPACE,
            capture_output=True,
            text=True,
            timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = result.stdout.strip()
        if result.stderr.strip():
            out = (out + "\n[stderr]\n" + result.stderr.strip()).strip()
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: execution timed out (15s limit)."
    except Exception as e:
        return f"Error: {e}"


FETCH_TOOLS = [
    web_search_tool,
    wikipedia_summary_tool,
    read_file_tool,
    list_files_tool,
    search_documents_tool,
]

QUANT_TOOLS = [
    calculator_tool,
    run_python_tool,
]
