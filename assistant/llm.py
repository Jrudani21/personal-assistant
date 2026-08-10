"""Ollama chat wrapper with a tool-calling loop. Runs 100% local, no API cost."""
import datetime
import json
import re

import ollama

from . import memory, observations, reminders
from .tools import REGISTRY, SCHEMAS

_OVERFLOW_RE = re.compile(r"prompt too long; exceeded (?:max )?context length", re.IGNORECASE)


def _describe_error(e: Exception) -> str:
    if _OVERFLOW_RE.search(str(e)):
        return "This chat is too long for the model's context window. Start a new chat, or ask a shorter question."
    return f"Error talking to Ollama: {e}"

BASE_SYSTEM_PROMPT = (
    "You are Janak's personal AI assistant, running fully locally via Ollama "
    "(no API cost). Be direct and concise. Use tools when you need current "
    "info, math, or file access. Use 'remember' whenever the user shares a "
    "durable fact/preference about themselves. "
    "Never invent tool results — call the tool. "
    "When you answer using search_documents results, cite the numbered "
    "sources inline like [1], [2] matching the tool output's numbering."
)

MAX_TOOL_ROUNDS = 6
MAX_MEMORY_FACTS = 30  # cap on facts injected into the system prompt (prompt crowding)


def _system_prompt() -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M %A")
    prompt = f"{BASE_SYSTEM_PROMPT}\n\nCurrent date/time: {now}. Use this to resolve relative dates (\"tomorrow\", \"in 2 hours\") — do not guess or use your training cutoff."
    facts = memory.recent_entries(MAX_MEMORY_FACTS)
    if facts:
        lines = []
        for entry in facts:
            line = f"- {entry['key']}: {entry['value']}"
            if entry["facts"]:
                line += "  [" + "; ".join(entry["facts"]) + "]"
            if entry["concepts"]:
                line += "  (" + ", ".join(entry["concepts"]) + ")"
            lines.append(line)
        prompt += f"\n\nKnown facts about the user (already remembered, no need to call recall for these):\n{'\n'.join(lines)}"
        total = len(memory.list_memory())
        hidden = total - len(facts)
        if hidden > 0:
            prompt += f"\n...and {hidden} more stored fact{'s' if hidden != 1 else ''} — ask the user or call recall if one of these seems relevant."
    due = reminders.due_reminders()
    if due:
        lines = "\n".join(f"- {r['text']} (was due {r['due_at']})" for r in due)
        prompt += f"\n\nReminders that are now due — mention these to the user this turn:\n{lines}"
        for r in due:
            reminders.mark_fired(r["id"])
    return prompt


def run_chat(model: str, history: list[dict], on_tool_call=None):
    """history: list of {'role': 'user'|'assistant', 'content': str}.
    Returns the final assistant reply text. Streams nothing; used for a
    simple request/response Streamlit turn."""
    messages = [{"role": "system", "content": _system_prompt()}] + history

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = ollama.chat(model=model, messages=messages, tools=SCHEMAS)
        except Exception as e:
            return _describe_error(e)
        msg = response["message"]
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            return msg.get("content", "")

        messages.append(msg)
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args or "{}")
            fn = REGISTRY.get(name)
            result = fn(**args) if fn else f"Unknown tool: {name}"
            observations.append(name, args, result)
            if on_tool_call:
                on_tool_call(name, args, result)
            messages.append({"role": "tool", "content": str(result), "name": name})

    return "Reached max tool-call rounds without a final answer."


def stream_chat(model: str, history: list[dict], on_tool_call=None):
    """Generator version: yields text chunks as they're generated so the
    UI can render them live. Tool-call rounds execute synchronously
    in between (they have no visible content to stream)."""
    messages = [{"role": "system", "content": _system_prompt()}] + history

    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            stream = ollama.chat(model=model, messages=messages, tools=SCHEMAS, stream=True)
        except Exception as e:
            yield f"\n\n_{_describe_error(e)}_"
            return

        content = ""
        tool_calls = None

        try:
            for chunk in stream:
                msg = chunk["message"]
                piece = msg.get("content")
                if piece:
                    content += piece
                    yield piece
                if msg.get("tool_calls"):
                    tool_calls = msg["tool_calls"]
        except Exception as e:
            yield f"\n\n_{_describe_error(e)}_"
            return

        if not tool_calls:
            return

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args or "{}")
            fn = REGISTRY.get(name)
            result = fn(**args) if fn else f"Unknown tool: {name}"
            observations.append(name, args, result)
            if on_tool_call:
                on_tool_call(name, args, result)
            messages.append({"role": "tool", "content": str(result), "name": name})

    yield "\n\n_Reached max tool-call rounds without a final answer._"
