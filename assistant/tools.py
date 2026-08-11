"""Tool implementations + schemas for the assistant's tool-calling loop."""
import ast
import json
import operator
import datetime
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import requests
from ddgs import DDGS

from . import backup
from . import config as _config
from . import memory
from . import observations
from . import rag
from . import reminders
from . import repl
from . import todo

WORKSPACE = Path(__file__).resolve().parent.parent / "data" / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)


def _workspace() -> Path:
    """Configured workspace dir (config.json > default). Created on demand."""
    cfg = _config.get("workspace_dir")
    p = Path(cfg).resolve() if cfg else WORKSPACE
    p.mkdir(parents=True, exist_ok=True)
    return p

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Unsupported expression.")


def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval").body
        return str(_safe_eval(tree))
    except Exception as e:
        return f"Error: {e}"


def web_search(query: str, max_results: int | None = None) -> str:
    if max_results is None:
        max_results = int(_config.get("web_search_max_results", 5))
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "No results found."
        lines = [f"- {r['title']}: {r['body']} ({r['href']})" for r in results]
        return "\n".join(lines)
    except Exception as e:
        return f"Search error: {e}"


def _resolve(path: str) -> Path:
    ws = _workspace()
    p = (ws / path).resolve()
    if ws not in p.parents and p != ws:
        raise ValueError("Path escapes workspace.")
    return p


def _truncate_head(text: str, max_chars: int | None = None) -> str:
    if max_chars is None:
        max_chars = int(_config.get("read_file_max_chars", 8000))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated, {len(text) - max_chars} more chars]"


def _truncate_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"[...{len(text) - max_chars} earlier chars truncated]\n" + text[-max_chars:]


def read_file(path: str) -> str:
    try:
        p = _resolve(path)
        if not p.exists():
            return f"File not found: {path}"
        return _truncate_head(p.read_text(encoding="utf-8"))
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    try:
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"


def deep_analysis(raw_input: str) -> str:
    """Runs a 4-step research pipeline (fetch facts -> verify/compute numbers
    -> analyze -> report) for questions that need more than a single-pass
    answer. The fetch step can pull from the web, Wikipedia, uploaded
    documents, or local workspace files; the verify step runs real Python/
    calculator checks on any numbers involved. Slower than other tools
    (~45-120s, the analysis step calls the DeepSeek API with a local fallback) -- only use
    this when the user explicitly wants deeper/multi-step analysis, not for
    quick questions."""
    try:
        from . import crew
        return crew.run_deep_analysis(raw_input)
    except Exception as e:
        return f"Error: {e}"


def clear_crew_cache() -> str:
    """Forget all cached deep-analysis reports so the next identical request
    runs the full pipeline again instead of returning the stored result."""
    try:
        from . import crew_cache
        return crew_cache.clear()
    except Exception as e:
        return f"Error: {e}"


# ---- researched tools: MCP, SQL, web fetch, audio, skills ---------------


def mcp_list_tools(server: str = "") -> str:
    """List tools exposed by configured MCP servers. With no server argument,
    lists tools from every configured server (nanobot/private-gpt research:
    plug external tools into the agent via MCP)."""
    try:
        from . import mcp as mcp_mod
        return mcp_mod.list_tools(server)
    except Exception as e:
        return f"Error: {e}"


def mcp_call_tool(server: str, tool_name: str, arguments: str = "{}") -> str:
    """Call a tool on an MCP server with JSON arguments."""
    try:
        from . import mcp as mcp_mod
        try:
            args = json.loads(arguments or "{}")
        except Exception as e:
            return f"Error: arguments must be valid JSON: {e}"
        if not isinstance(args, dict):
            return "Error: arguments must be a JSON object."
        return mcp_mod.call_tool(server, tool_name, args)
    except Exception as e:
        return f"Error: {e}"


def _sqlite_db() -> Path:
    cfg = _config.get("sqlite_db")
    if cfg:
        return Path(cfg)
    return Path(__file__).resolve().parent.parent / "data" / "assistant.db"


def query_sql(query: str) -> str:
    """Run a read-only SQL query against the assistant's local SQLite database
    (private-gpt research: text-to-SQL). SELECT/PRAGMA/EXPLAIN/WITH only;
    writes are refused. Results capped at 50 rows."""
    q = (query or "").strip()
    lowered = q.lower()
    if not q:
        return "Error: empty query."
    if not (lowered.startswith("select") or lowered.startswith("pragma")
            or lowered.startswith("explain") or lowered.startswith("with")):
        return "Error: only SELECT / PRAGMA / EXPLAIN / WITH queries are allowed."
    if ";" in q.rstrip().rstrip(";") or q.count(";") > 1:
        return "Error: multiple statements are not allowed."

    db = _sqlite_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    if lowered.startswith(("select", "with")) and " limit " not in lowered and not lowered.rstrip().endswith("limit"):
        q = f"SELECT * FROM ({q}) AS _q LIMIT 50"
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(50)
        conn.close()
        if not cols:
            return "(query returned no columns)"
        lines = [" | ".join(cols)]
        for r in rows:
            lines.append(" | ".join(str(v) for v in r))
        body = "\n".join(lines)
        if len(body) > 6000:
            body = body[:6000] + "\n...[truncated]"
        capped = " (capped at 50)" if len(rows) >= 50 else ""
        return body + f"\n({len(rows)} row{'s' if len(rows) != 1 else ''}{capped})"
    except Exception as e:
        return f"SQL error: {e}"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip > 0:
            self.skip -= 1

    def handle_data(self, data):
        if self.skip == 0:
            self.parts.append(data)


def fetch_webpage(url: str) -> str:
    """Fetch a URL and return its readable text (ragflow/awesome-llm-apps
    research: deep doc understanding — read web content directly)."""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (PersonalAssistant local)"})
        if r.status_code != 200:
            return f"Fetch error: HTTP {r.status_code}"
        parser = _TextExtractor()
        parser.feed(r.text)
        text = "\n".join(" ".join(ln.split()) for ln in "".join(parser.parts).splitlines())
        text = "\n".join(ln for ln in text.splitlines() if ln.strip())
        max_chars = int(_config.get("fetch_webpage_max_chars", 8000))
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...[truncated, {len(text) - max_chars} more chars]"
        return text.strip() or "(no readable text found on that page)"
    except Exception as e:
        return f"Fetch error: {e}"


def transcribe_file(path: str) -> str:
    """Transcribe an audio file (wav/mp3/m4a/ogg) from the workspace — voice
    input beyond the UI recorder (meetily research gap)."""
    try:
        from . import voice
        p = _resolve(path)
        if not p.exists():
            return f"File not found: {path}"
        text = voice.transcribe_file(p)
        return text.strip() or "(no speech detected)"
    except Exception as e:
        return f"Transcription error: {e}"


def list_skills() -> str:
    """List available skills (anything-llm/private-gpt pattern: reusable
    procedures the assistant can discover and run)."""
    try:
        from . import skills
        return skills.list_skills()
    except Exception as e:
        return f"Error: {e}"


def run_skill(name: str, args: str = "") -> str:
    """Run a skill: executes its run.py if present, otherwise returns its
    SKILL.md instructions so the model can follow them with normal tools."""
    try:
        from . import skills
        return skills.run_skill(name, args)
    except Exception as e:
        return f"Error: {e}"


def get_datetime() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")


def list_files(subpath: str = "") -> str:
    try:
        p = _resolve(subpath) if subpath else _workspace()
        if not p.exists():
            return f"Not found: {subpath}"
        entries = sorted(p.iterdir())
        if not entries:
            return "(empty)"
        return "\n".join(f"{'[dir] ' if e.is_dir() else ''}{e.name}" for e in entries)
    except Exception as e:
        return f"Error: {e}"


def weather(location: str) -> str:
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1}, timeout=10,
        ).json()
        results = geo.get("results")
        if not results:
            return f"Location not found: {location}"
        lat, lon = results[0]["latitude"], results[0]["longitude"]
        place = f"{results[0]['name']}, {results[0].get('country', '')}"
        fc = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                "temperature_unit": "celsius",
            }, timeout=10,
        ).json()
        cur = fc.get("current", {})
        return (
            f"{place}: {cur.get('temperature_2m')}C, "
            f"humidity {cur.get('relative_humidity_2m')}%, "
            f"wind {cur.get('wind_speed_10m')} km/h"
        )
    except Exception as e:
        return f"Weather error: {e}"


_WIKI_HEADERS = {"User-Agent": "PersonalAssistant/1.0 (local, https://github.com/; contact: local-user)"}


def wikipedia_summary(topic: str) -> str:
    try:
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(topic)}",
            headers=_WIKI_HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return f"No Wikipedia page found for '{topic}'."
        data = r.json()
        return data.get("extract", "No summary available.")
    except Exception as e:
        return f"Wikipedia error: {e}"


def run_python(code: str) -> str:
    """Runs Python in a persistent session (variables survive across calls,
    cwd locked to the workspace, 15s timeout per call). Not a real security
    sandbox — same trust level as any script you'd run yourself locally."""
    return _truncate_tail(repl.run_persistent(code), 4000)


def restart_python_session() -> str:
    return repl.restart()


def remember(key: str, value: str, facts: list[str] | None = None,
             concepts: list[str] | None = None) -> str:
    return memory.remember(key, value, facts, concepts)


def recall(key: str) -> str:
    return memory.recall(key)


def forget(key: str) -> str:
    return memory.forget(key)


def recent_activity(limit: int = 10) -> str:
    """Returns the assistant's own recent tool calls (what it has actually
    done), newest batch first — useful for recalling earlier actions in the
    same or previous sessions."""
    entries = observations.read(limit)
    if not entries:
        return "No tool activity recorded yet."
    lines = []
    for e in entries:
        args = e.get("args", "")
        result = e.get("result", "")
        if e.get("args_truncated"):
            args += " [truncated]"
        if e.get("result_truncated"):
            result += " [truncated]"
        lines.append(f"[{e.get('timestamp', '?')}] {e.get('tool')}({args}) → {result}")
    return "\n".join(lines)


def distill_memory() -> str:
    """Learn from the assistant's own recent activity: extract durable facts
    about the user from the tool-call log and add NEW keys to memory. Existing
    memory is never overwritten. Runs a local-model call (~5-15s)."""
    try:
        from . import distill
        return distill.distill()
    except Exception as e:
        return f"Error: {e}"


def backup_data() -> str:
    """Snapshot the assistant's entire data folder (memory, chats,
    observations, embeddings, tasks, reminders, workspace) into a local,
    timestamped backup. Keeps the newest 10 backups and prunes older ones.
    Use before major changes or just to keep a local history."""
    try:
        return backup.create_backup()
    except Exception as e:
        return f"Error: {e}"


def add_task(text: str) -> str:
    return todo.add_task(text)


def list_tasks() -> str:
    return todo.list_tasks()


def complete_task(task_id: int) -> str:
    return todo.complete_task(task_id)


def clear_tasks() -> str:
    return todo.clear_tasks()


def remind_me(text: str, due_at: str) -> str:
    return reminders.remind_me(text, due_at)


def list_reminders() -> str:
    return reminders.list_reminders()


def cancel_reminder(reminder_id: int) -> str:
    return reminders.cancel_reminder(reminder_id)


REGISTRY = {
    "calculator": calculator,
    "web_search": web_search,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "get_datetime": get_datetime,
    "remember": remember,
    "recall": recall,
    "forget": forget,
    "add_task": add_task,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
    "clear_tasks": clear_tasks,
    "remind_me": remind_me,
    "list_reminders": list_reminders,
    "cancel_reminder": cancel_reminder,
    "weather": weather,
    "wikipedia_summary": wikipedia_summary,
    "run_python": run_python,
    "restart_python_session": restart_python_session,
    "search_documents": rag.search_documents,
    "sync_vault": rag.sync_vault,
    "sync_knowledge": rag.sync_knowledge,
    "deep_analysis": deep_analysis,
    "clear_crew_cache": clear_crew_cache,
    "recent_activity": recent_activity,
    "distill_memory": distill_memory,
    "backup_data": backup_data,
    "mcp_list_tools": mcp_list_tools,
    "mcp_call_tool": mcp_call_tool,
    "query_sql": query_sql,
    "fetch_webpage": fetch_webpage,
    "transcribe_file": transcribe_file,
    "list_skills": list_skills,
    "run_skill": run_skill,
}

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a numeric arithmetic expression (+ - * / ** % //).",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "e.g. '12 * (3 + 4)'"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information (news, facts, prices, anything not in your training data).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "default 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the assistant's local workspace folder.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "relative path inside workspace"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write/overwrite a text file in the assistant's local workspace folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files/folders inside the assistant's workspace (or a subfolder of it).",
            "parameters": {
                "type": "object",
                "properties": {"subpath": {"type": "string", "description": "relative subfolder, default root"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Get current weather for a city/location, no API key needed.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "e.g. 'Winnipeg'"}},
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_summary",
            "description": "Get a short Wikipedia summary of a topic/person/place.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Run a Python snippet (e.g. pandas/data analysis). Variables, imports, and dataframes PERSIST across calls in this session — you can load data once and build on it across multiple calls, no need to redefine everything each time. Use print() to return output. 15s timeout per call, cwd is the workspace folder.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "full Python source to execute"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_python_session",
            "description": "Wipe all variables/imports from the persistent Python session and start fresh. Use if state gets corrupted or the user asks to reset it.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Search the user's own knowledge base for relevant passages — both uploaded documents (PDF/txt/md) and every note in their Obsidian vault. Use this whenever the user asks about something they've written down, uploaded, or noted previously, or refers to their notes/vault/second brain.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_vault",
            "description": "Re-index the user's Obsidian vault so newly written or edited notes become searchable. Only needed if the user says they just wrote/changed a note and it isn't showing up in search results — unchanged notes are skipped automatically.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_knowledge",
            "description": "Re-index the nightly-learn knowledge base (deepseek-cave) so newly scraped knowledge becomes searchable. Deduplicates re-scraped facts automatically. Only needed after the nightly-learn crew has run and the user wants the new knowledge available.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_analysis",
            "description": "Run a 4-step research pipeline (fetch facts -> verify/compute numbers -> analyze -> write report) for questions needing deeper multi-step reasoning than a single reply. Can pull from the web, Wikipedia, uploaded documents, or local workspace files, and runs real calculations rather than guessing numbers. Slow (~45-120s) -- only use when the user explicitly wants deep/thorough analysis.",
            "parameters": {
                "type": "object",
                "properties": {"raw_input": {"type": "string", "description": "the topic, question, or file/document reference to research and analyze"}},
                "required": ["raw_input"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_crew_cache",
            "description": "Delete all cached deep-analysis reports. The next deep_analysis call for any previously analyzed topic will re-run the full pipeline instead of returning the stored result. Use when the user wants a fresh take on something already analyzed, or the underlying data changed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Get the current local date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save a fact/preference about the user permanently under a short key, for recall in future sessions. Optionally attach discrete facts (specific claims, e.g. 'GPU: RTX 4060 8 GB') and concepts (tags, e.g. 'hardware') to make the memory structured and retrievable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "short, stable identifier, e.g. 'favorite_language'. Reusing an existing key silently OVERWRITES its value — it does not append or error."},
                    "value": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}, "description": "optional discrete claims, each a single verifiable statement"},
                    "concepts": {"type": "array", "items": {"type": "string"}, "description": "optional short tags for retrieval/dedup, e.g. 'hardware', 'preferences'"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backup_data",
            "description": "Snapshot the assistant's entire local data (memory, chats, observations, embeddings, tasks, reminders, workspace files) into a timestamped backup folder. Keeps the newest 10 backups. Use before major changes or whenever the user asks to save/back up their data locally.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "distill_memory",
            "description": "Learn from the assistant's own recent activity: read the tool-call log and extract durable facts about the user into memory (new keys only, existing memory is never overwritten). Use occasionally to build memory from what the assistant has actually done. Takes ~5-15s (one local-model call).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_activity",
            "description": "List the assistant's own recent tool calls (what it has actually done in this or previous sessions) — useful for recalling earlier actions, files touched, or searches run.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "how many recent tool calls to return, default 10"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Retrieve a previously remembered fact by key.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": "Delete a previously remembered fact by key, e.g. when the user says it's no longer true or asks you to forget it.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a task to the user's local to-do scratchpad, e.g. for multi-step goals or things to follow up on.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks in the user's local to-do scratchpad, with done/not-done status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task done by its numeric id. IDs are not reordered or reused after deletion — if unsure of the current id, call list_tasks first rather than guessing.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_tasks",
            "description": "Delete all tasks from the to-do scratchpad.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remind_me",
            "description": "Set a reminder that surfaces in the UI and to you once due. Only fires while the app is open (no background daemon).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "due_at": {"type": "string", "description": "ISO datetime, e.g. '2026-08-09 14:00'"},
                },
                "required": ["text", "due_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List all reminders with pending/fired status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel a reminder by its numeric id. IDs are not reordered or reused after cancellation — if unsure of the current id, call list_reminders first rather than guessing.",
            "parameters": {
                "type": "object",
                "properties": {"reminder_id": {"type": "integer"}},
                "required": ["reminder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_list_tools",
            "description": "List tools exposed by configured MCP servers (external tool servers, e.g. filesystem, database, browser). With no server argument, lists tools from every configured server. Call this first to discover what's available before calling mcp_call_tool.",
            "parameters": {
                "type": "object",
                "properties": {"server": {"type": "string", "description": "optional server name to filter by"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call_tool",
            "description": "Call a tool on a configured MCP server. Use mcp_list_tools first to discover server and tool names. Arguments must be a JSON object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP server name"},
                    "tool_name": {"type": "string", "description": "tool name on that server"},
                    "arguments": {"type": "string", "description": "JSON object string, e.g. '{\"path\": \"C:/x\"}'"},
                },
                "required": ["server", "tool_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sql",
            "description": "Run a read-only SQL query (SELECT/PRAGMA/EXPLAIN/WITH) against the assistant's local SQLite database (data/assistant.db). Use for structured data the user has stored there. Writes are refused; results capped at 50 rows.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "read-only SQL, e.g. 'SELECT * FROM notes LIMIT 10'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "Fetch a URL and return its readable text (stripped of markup). Use when web_search snippets aren't enough and you need the actual page content.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_file",
            "description": "Transcribe an audio file (wav/mp3/m4a/ogg) from the workspace into text. Use when the user drops a recording/voice note in the workspace and wants it transcribed.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "relative path inside workspace"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List available skills (reusable procedures the user has set up in data/skills). Call this to discover what skills exist before running one.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "Run a skill by name. If the skill has a run.py script it executes and returns its output; otherwise it returns the skill's instructions for you to follow with your normal tools. Optionally pass a single string argument.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "args": {"type": "string", "description": "optional single string argument"},
                },
                "required": ["name"],
            },
        },
    },
]

# Admin tool — lets the assistant administer the AI fleet (status, crew sweeps,
# docker, logs, start Open WebUI). Kept separate so tools.py stays clean.
from . import admin as _admin_tool

REGISTRY = {**REGISTRY, **_admin_tool.get_registry_extra()}
SCHEMAS = [*SCHEMAS, _admin_tool.get_schemas_extra()]


def get_registry() -> dict:
    """Tool callables, filtered by the enabled_tools config ([] = all)."""
    return {name: fn for name, fn in REGISTRY.items() if _config.tool_enabled(name)}


def get_schemas() -> list[dict]:
    """Tool schemas for Ollama, filtered by the enabled_tools config."""
    return [s for s in SCHEMAS if _config.tool_enabled(s["function"]["name"])]
