"""Tool implementations + schemas for the assistant's tool-calling loop."""
import ast
import operator
import datetime
from pathlib import Path

import requests
from ddgs import DDGS

from . import memory
from . import rag
from . import reminders
from . import repl
from . import todo

WORKSPACE = Path(__file__).resolve().parent.parent / "data" / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

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


def web_search(query: str, max_results: int = 5) -> str:
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "No results found."
        lines = [f"- {r['title']}: {r['body']} ({r['href']})" for r in results]
        return "\n".join(lines)
    except Exception as e:
        return f"Search error: {e}"


def _resolve(path: str) -> Path:
    p = (WORKSPACE / path).resolve()
    if WORKSPACE not in p.parents and p != WORKSPACE:
        raise ValueError("Path escapes workspace.")
    return p


def _truncate_head(text: str, max_chars: int = 8000) -> str:
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


def get_datetime() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")


def list_files(subpath: str = "") -> str:
    try:
        p = _resolve(subpath) if subpath else WORKSPACE
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


def remember(key: str, value: str) -> str:
    return memory.remember(key, value)


def recall(key: str) -> str:
    return memory.recall(key)


def forget(key: str) -> str:
    return memory.forget(key)


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
            "description": "Search the user's uploaded documents (PDF/txt/md) for relevant passages. Use this whenever the user asks about content from a file they uploaded.",
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
            "name": "get_datetime",
            "description": "Get the current local date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save a fact/preference about the user permanently under a short key, for recall in future sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "short, stable identifier, e.g. 'favorite_language'. Reusing an existing key silently OVERWRITES its value — it does not append or error."},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
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
]
