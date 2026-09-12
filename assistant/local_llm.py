"""Local model transport: LM Studio (OpenAI-compatible) with an Ollama fallback.

Why this module exists
----------------------
The app was written against Ollama's NATIVE API (`ollama.chat`, `ollama.embeddings`,
`ollama.list`). Ollama was uninstalled on 2026-09-01, and the healthy local tier on
this machine is **LM Studio**, which speaks the OpenAI protocol and does NOT serve
Ollama's native routes: `POST /api/chat` answers HTTP 200 with an
"Unexpected endpoint or method" error body. So repointing a URL at LM Studio looks
like it worked (and `GET /api/tags` even answers 200) while every generation fails.

This is the single local transport. It resolves a backend lazily — LM Studio first,
then a genuinely-live Ollama, for hosts that still run one — and speaks whichever
protocol answered.

The public signatures mirror the `ollama` subset the app used, so call sites only
change their import and module prefix:

    chat(model, messages, tools=)          -> {"message": {"content", "tool_calls"}}
    chat_stream(model, messages, tools=)   -> generator of the same chunk shape
    embeddings(model, prompt)              -> {"embedding": [...]}
    list_models()                          -> {"models": [{"model": ...}, ...]}

NOTE the name: this module must NOT define a bare `list`, because a module-level
`list` shadows the builtin for every function in the file (it broke
`list(embedding)` inside embeddings() with "list() takes 0 positional arguments").
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import requests

from . import config as _config

LMSTUDIO_DEFAULT = "http://127.0.0.1:1234/v1"   # note the /v1 — required, see below
OLLAMA_DEFAULT = "http://localhost:11434"
PROBE_TIMEOUT_S = 1
#: OpenAI-compatible clients insist on a non-empty key; the local server ignores it.
LOCAL_API_KEY = "lm-studio"

#: Verdicts are cached because every call would otherwise pay a probe.
_BACKEND: str | None = None


def lmstudio_base() -> str:
    """LM Studio OpenAI base URL. The trailing /v1 is required: this client hands
    base_url straight to the OpenAI SDK, which appends only the route
    (/chat/completions), not the version prefix."""
    base = str(_config.get("lmstudio_base_url", LMSTUDIO_DEFAULT) or LMSTUDIO_DEFAULT)
    base = base.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def ollama_base() -> str:
    return str(_config.get("ollama_base_url", OLLAMA_DEFAULT) or OLLAMA_DEFAULT).rstrip("/")


def _lmstudio_root() -> str:
    """LM Studio's host root. Its management API lives at the root, NOT under /v1:
    /api/v0/models is http://host:1234/api/v0/models while the OpenAI-compatible
    routes are http://host:1234/v1/*. Getting this wrong silently yields an empty
    model list rather than an error."""
    base = lmstudio_base()
    return base[:-3] if base.endswith("/v1") else base


def _lmstudio_up() -> bool:
    try:
        return requests.get(f"{lmstudio_base()}/models", timeout=PROBE_TIMEOUT_S).status_code == 200
    except Exception:
        return False


def _ollama_up() -> bool:
    try:
        return requests.get(f"{ollama_base()}/api/tags", timeout=PROBE_TIMEOUT_S).status_code == 200
    except Exception:
        return False


def backend(refresh: bool = False) -> str:
    """Resolve and cache the local backend: 'lmstudio', 'ollama', or 'none'."""
    global _BACKEND
    if _BACKEND is None or refresh:
        if _lmstudio_up():
            _BACKEND = "lmstudio"
        elif _ollama_up():
            _BACKEND = "ollama"
        else:
            _BACKEND = "none"
    return _BACKEND


def describe() -> str:
    """Human-readable status, for diagnostics and the Settings page."""
    name = backend(refresh=True)
    if name == "lmstudio":
        return f"LM Studio (OpenAI-compatible) at {lmstudio_base()}"
    if name == "ollama":
        return f"Ollama at {ollama_base()}"
    return f"no local server (probed LM Studio {lmstudio_base()} and Ollama {ollama_base()})"


def api_key() -> str:
    """Key for any OpenAI-compatible client pointed at the local server."""
    return str(_config.get("lmstudio_api_key", LOCAL_API_KEY) or LOCAL_API_KEY)


def chat_model() -> str:
    """The chat model to use when the caller expresses no preference: an explicit
    config override, else the first chat-capable model the live backend serves —
    never an embedding model, which cannot answer a chat call."""
    override = str(_config.get("local_chat_model", "") or "")
    if override:
        return override
    for mid, mtype in _served_models():
        if mid and mtype != "embeddings":
            return mid
    return "qwen/qwen3-8b"


# --------------------------------------------------------------------------- #
# model list
# --------------------------------------------------------------------------- #
def _served_models() -> list[tuple[str, str]]:
    """[(id, type)] as LM Studio reports them. Uses /api/v0/models, which — unlike
    /v1/models — labels each entry 'llm' or 'embeddings'. That label matters: an
    auto-pick of "the first model" would otherwise be free to hand an embedding
    model to a chat call."""
    try:
        r = requests.get(f"{_lmstudio_root()}/api/v0/models", timeout=PROBE_TIMEOUT_S)
        if r.status_code == 200:
            return [(m.get("id", ""), m.get("type", "llm")) for m in r.json().get("data", [])]
    except Exception:
        pass
    try:
        r = requests.get(f"{lmstudio_base()}/models", timeout=PROBE_TIMEOUT_S)
        if r.status_code == 200:
            return [(m.get("id", ""), "llm") for m in r.json().get("data", [])]
    except Exception:
        pass
    return []


def resolve_model(requested: str) -> str:
    """Map a requested model name onto what the live backend actually serves.

    The config still carries Ollama-era names (`nomic-embed-text`, `qwen2.5:7b`),
    and LM Studio serves different ids (`text-embedding-nomic-embed-text-v1.5`).
    Exact match wins; otherwise a containment match.

    An unmatched request is passed through UNCHANGED — and note what that means on
    LM Studio, because it is not what an earlier version of this docstring claimed:
    the server does NOT error on an unknown model id, it answers from whatever is
    resident. Measured (adversarial review, 2026-09-12): `bogus-model-xyz` returned
    HTTP 200 and the identical 768-dim vector to the real embedder, and a chat call
    with a bogus id answered as `qwen/qwen3-8b`. So a typo'd model name yields
    plausible output from the wrong model with no signal anywhere. Raising instead
    was considered and rejected: /api/v0/models lists LOADED instances, so a
    legitimate installed-but-unloaded id would start erroring. Treat a passthrough
    as untrusted output, not as a validated one.
    """
    if backend() != "lmstudio":
        return requested
    served = [mid for mid, _ in _served_models() if mid]
    if not served or requested in served:
        return requested
    stem = requested.split(":")[0].lower()
    matches = [
        mid for mid in served
        if stem and (stem in mid.lower()
                     or mid.lower().split("/")[-1].split(":")[0] == stem)
    ]
    # Shortest wins, to avoid the ":N" instance suffix — NOT because the shorter id
    # is better. Measured 2026-09-12: the plain id was the NOT-loaded instance and
    # ":2" was the loaded one. The suffix is an instance counter that appears when
    # `lms load` stacks a second copy, not a quality marker.
    return sorted(matches, key=len)[0] if matches else requested


def list_models() -> dict:
    """Ollama-shaped model list. Chat-capable models come first, so the existing
    'auto-pick the first model' default lands on a chat model, not an embedder."""
    name = backend()
    if name == "ollama":
        import ollama
        return ollama.list()
    typed = _served_models()
    if not typed:
        return {"models": []}
    ordered = [m for m, t in typed if t != "embeddings"] + [m for m, t in typed if t == "embeddings"]
    return {"models": [{"model": mid, "name": mid} for mid in ordered if mid]}


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Translate the app's Ollama-shaped history into what the OpenAI endpoint accepts.

    Callers build history the way `ollama.chat` wanted it: an assistant turn carries
    `tool_calls` whose `function.arguments` is a DICT and has no call id, and each
    tool result is `{"role": "tool", "content": ..., "name": <tool>}`. OpenAI wants
    the opposite — a per-call `id`, `arguments` as a JSON *string*, and tool results
    keyed by `tool_call_id`. Passing the Ollama shape straight through 400s with
    "Invalid 'messages' in payload", and it only happens on the SECOND round of a
    tool loop, i.e. exactly when a tool has been used.

    Results are matched to calls by tool NAME, not by position. They are produced by
    separate invocations and can come back in any order; a positional match silently
    attaches a result to the wrong call, and LM Studio accepts that without
    complaint (measured: reverse-order and cross-round mis-pairs both returned 200).
    """
    out: list[dict] = []
    pending: list[tuple[str, str]] = []   # (call_id, tool_name) awaiting a result
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            # A new tool-call turn invalidates anything still pending: those calls
            # can no longer be answered, and pairing their ids with this round's
            # results would attach the wrong output to the wrong tool.
            pending.clear()
            calls = []
            for j, call in enumerate(m["tool_calls"]):
                fn = call.get("function", {}) or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args if args is not None else {})
                call_id = call.get("id") or f"call_{i}_{j}"
                calls.append({"id": call_id, "type": "function",
                              "function": {"name": fn.get("name", ""), "arguments": args}})
                pending.append((call_id, str(fn.get("name", ""))))
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": calls})
        elif role == "tool":
            call_id = m.get("tool_call_id")
            name = str(m.get("name") or "")
            if call_id:
                pending[:] = [p for p in pending if p[0] != call_id]
            elif pending:
                idx = next((k for k, (_, n) in enumerate(pending) if n and n == name), 0)
                call_id = pending.pop(idx)[0]
            else:
                # Nothing to attach this to — the OpenAI shape has no way to express
                # an orphan tool result. Kept as a placeholder rather than dropped so
                # the content still reaches the model.
                call_id = "call_unknown"
            out.append({"role": "tool", "tool_call_id": call_id,
                        "content": m.get("content") if m.get("content") is not None else ""})
        else:
            out.append({"role": role or "user", "content": m.get("content", "")})
    return out


def _client():
    from openai import OpenAI
    # LM Studio ignores the key's value, but the SDK insists on a non-empty one.
    return OpenAI(base_url=lmstudio_base(), api_key=api_key())


def _is_tool_support_error(e: Exception) -> bool:
    """True only for "this server/model cannot do tool calls".

    Exists so the tools-less retry in chat()/chat_stream() cannot swallow a
    transient 500 or a timeout and silently turn a tool-capable turn into a
    tools-less one.
    """
    text = f"{type(e).__name__}: {e}".lower()
    if "tool" not in text:
        return False
    return any(marker in text for marker in (
        "does not support", "not supported", "unsupported", "no tool",
        "tool_choice", "tools is not", "invalid tool", "tool template",
    ))


def _ollama_shape(message) -> dict:
    """OpenAI message -> the `ollama.chat` reply shape the app already consumes."""
    calls = []
    for tc in (getattr(message, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        raw = (getattr(fn, "arguments", "") or "") if fn else ""
        try:
            args = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
        except json.JSONDecodeError:
            args = {}
        calls.append({"function": {"name": getattr(fn, "name", ""), "arguments": args}})
    return {"role": "assistant", "content": message.content or "", "tool_calls": calls or None}


def _ollama_opts(opts: dict) -> dict:
    """Translate OpenAI-style call options into Ollama's `options` dict.

    Without this a caller passing `max_tokens=` would TypeError on the Ollama
    branch, which is why the earlier migration silently dropped those parameters
    instead of forwarding them.
    """
    mapping = {"max_tokens": "num_predict", "temperature": "temperature", "top_p": "top_p"}
    converted = {mapping[k]: v for k, v in opts.items() if k in mapping}
    if not converted:
        return opts
    rest = {k: v for k, v in opts.items() if k not in mapping}
    rest["options"] = {**converted, **(rest.get("options") or {})}
    return rest


def chat(model: str, messages: list[dict], tools: list[dict] | None = None, **opts) -> dict:
    """Non-streaming chat. Returns {"message": {"content", "tool_calls"}}.

    `**opts` are forwarded to the OpenAI client (e.g. `max_tokens`, `temperature`),
    and translated to Ollama's `options` on the fallback branch.
    """
    if backend() == "ollama":
        import ollama
        return ollama.chat(model=model, messages=messages, tools=tools,
                           **_ollama_opts(dict(opts)))

    client = _client()
    model = resolve_model(model)
    kwargs = {"tools": tools} if tools else {}
    messages = _to_openai_messages(messages)
    try:
        resp = client.chat.completions.create(model=model, messages=messages,
                                              **kwargs, **opts)
    except Exception as e:
        if not kwargs or not _is_tool_support_error(e):
            raise
        # Degrade to a plain completion ONLY when the model/server genuinely has no
        # tool support (see _is_tool_support_error).
        resp = client.chat.completions.create(model=model, messages=messages, **opts)
    return {"message": _ollama_shape(resp.choices[0].message)}


def chat_stream(model: str, messages: list[dict], tools: list[dict] | None = None, **opts):
    """Streaming chat. Yields `ollama`-shaped chunks; a final chunk carries
    `tool_calls` (accumulated from deltas) when the model asked for tools."""
    if backend() == "ollama":
        import ollama
        yield from ollama.chat(model=model, messages=messages, tools=tools,
                               stream=True, **_ollama_opts(dict(opts)))
        return

    client = _client()
    model = resolve_model(model)
    kwargs = {"tools": tools} if tools else {}
    messages = _to_openai_messages(messages)
    try:
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs, **opts)
    except Exception as e:
        if not kwargs or not _is_tool_support_error(e):
            raise
        stream = client.chat.completions.create(model=model, messages=messages,
                                                stream=True, **opts)

    # OpenAI streams tool calls as fragments keyed by index; name and arguments
    # both arrive split across deltas, so accumulate by index.
    calls: dict[int, dict] = {}
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield {"message": {"content": delta.content, "tool_calls": None}}
        for tc in (delta.tool_calls or []):
            slot = calls.setdefault(tc.index, {"name": "", "arguments": ""})
            if tc.function and tc.function.name:
                slot["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                slot["arguments"] += tc.function.arguments

    if calls:
        out = []
        for idx in sorted(calls):
            raw = calls[idx]["arguments"]
            try:
                args = json.loads(raw or "{}")
            except json.JSONDecodeError:
                args = {}
            out.append({"function": {"name": calls[idx]["name"], "arguments": args}})
        yield {"message": {"content": "", "tool_calls": out}}


# --------------------------------------------------------------------------- #
# embeddings
# --------------------------------------------------------------------------- #
def _lms_exe() -> Path | None:
    for cand in (Path.home() / ".lmstudio" / "bin" / "lms.exe",
                 Path.home() / ".lmstudio" / "bin" / "lms"):
        if cand.exists():
            return cand
    return None


def ensure_model_loaded(model: str) -> bool:
    """Ask LM Studio's CLI to load `model` into VRAM.

    Needed because LM Studio does NOT just-in-time load an embedding model: an
    embeddings call for an unloaded one fails with "No models loaded" even while a
    chat model is resident. Best-effort — a missing CLI returns False so the
    caller re-raises the original server error instead of masking it.
    """
    exe = _lms_exe()
    if exe is None:
        return False
    try:
        done = subprocess.run([str(exe), "load", model, "--gpu", "max"],
                              capture_output=True, timeout=180)
        return done.returncode == 0
    except Exception:
        return False


def embeddings(model: str, prompt: str) -> dict:
    """Returns {"embedding": [...]} — the shape `rag.py` already unpacks."""
    if backend() == "ollama":
        import ollama
        return ollama.embeddings(model=model, prompt=prompt)

    resolved = resolve_model(model)
    client = _client()
    try:
        resp = client.embeddings.create(model=resolved, input=prompt)
    except Exception as e:
        # An unloaded embedder is the common case; load it once, then re-resolve
        # (LM Studio may only accept the ":N" instance id once it is resident).
        if "no model" not in str(e).lower() or not ensure_model_loaded(resolved):
            raise
        resp = client.embeddings.create(model=resolve_model(resolved), input=prompt)
    return {"embedding": list(resp.data[0].embedding)}
