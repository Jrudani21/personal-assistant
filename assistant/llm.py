"""Ollama chat wrapper with a tool-calling loop. Runs 100% local, no API cost."""
import json
import ollama

from . import memory
from .tools import REGISTRY, SCHEMAS

BASE_SYSTEM_PROMPT = (
    "You are Janak's personal AI assistant, running fully locally via Ollama "
    "(no API cost). Be direct and concise. Use tools when you need current "
    "info, math, or file access. Use 'remember' whenever the user shares a "
    "durable fact/preference about themselves. "
    "Never invent tool results — call the tool."
)

MAX_TOOL_ROUNDS = 6


def _system_prompt() -> str:
    mem = memory.list_memory()
    if not mem:
        return BASE_SYSTEM_PROMPT
    facts = "\n".join(f"- {k}: {v}" for k, v in mem.items())
    return f"{BASE_SYSTEM_PROMPT}\n\nKnown facts about the user (already remembered, no need to call recall for these):\n{facts}"


def run_chat(model: str, history: list[dict], on_tool_call=None):
    """history: list of {'role': 'user'|'assistant', 'content': str}.
    Returns the final assistant reply text. Streams nothing; used for a
    simple request/response Streamlit turn."""
    messages = [{"role": "system", "content": _system_prompt()}] + history

    for _ in range(MAX_TOOL_ROUNDS):
        response = ollama.chat(model=model, messages=messages, tools=SCHEMAS)
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
        stream = ollama.chat(model=model, messages=messages, tools=SCHEMAS, stream=True)
        content = ""
        tool_calls = None

        for chunk in stream:
            msg = chunk["message"]
            piece = msg.get("content")
            if piece:
                content += piece
                yield piece
            if msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]

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
            if on_tool_call:
                on_tool_call(name, args, result)
            messages.append({"role": "tool", "content": str(result), "name": name})

    yield "\n\n_Reached max tool-call rounds without a final answer._"
