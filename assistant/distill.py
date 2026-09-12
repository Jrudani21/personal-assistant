"""Memory distillation — "Dream-lite".

The assistant learns from its own activity: reads the recent observation log
(tool calls it actually made), asks the local model to extract durable facts
about the user, and merges NEW keys into memory.json. This is nanobot's
Dream-run idea, scoped down to fit a single-user local assistant (see
brain/notes/Memory system design study.md).

Guardrails:
- Only NEW keys are added — existing memory is curated/user-owned and is
  never overwritten by distillation.
- The model must reply with strict JSON; anything else is rejected, and a
  failed run degrades to a clear message rather than corrupting memory.
- At most MAX_NEW_KEYS keys per run; keys must be sane identifiers.
"""
import json
import re

from . import local_llm as _local

from . import memory, observations

# Local chat models to prefer, best first. The LM Studio id comes first; the
# others remain for hosts that still run Ollama.
# Only the live model belongs here. The previous entries ("deepseek-r1:14b",
# "deepseek-r1:7b") were Ollama-era names, and Ollama no longer runs on this box
# (port 11434 dead). resolve_model() passes an unmatched id through UNCHANGED, and
# LM Studio answers a bogus id from whatever model is resident with HTTP 200 — so a
# dead fallback name produced plausible output from the wrong model, silently.
DEFAULT_MODEL_PRIORITY = ("qwen/qwen3.5-9b",)
MAX_NEW_KEYS = 5
KEY_RE = re.compile(r"^[a-z0-9_]{1,48}$")

DISTILL_SYSTEM_PROMPT = (
    "You are distilling durable facts about a user from a log of an AI "
    "assistant's own tool calls. Extract facts about the user's identity, "
    "preferences, hardware, projects, workflow, and goals. Skip one-off "
    "transient actions (a single web search, a single calculation). "
    "Respond with ONLY a JSON array, no prose, no markdown fences. "
    "Each element: "
    '{"key": "short_snake_case_identifier", "value": "short summary", '
    '"facts": ["discrete verifiable claim", ...], '
    '"concepts": ["tag", ...]}. '
    f"At most {MAX_NEW_KEYS} elements. If nothing durable is present, "
    "respond with []."
)


def _pick_model() -> str | None:
    try:
        available = {m["model"] for m in _local.list_models().get("models", [])}
    except Exception:
        available = set()
    for candidate in DEFAULT_MODEL_PRIORITY:
        if candidate in available:
            return candidate
    return next(iter(available), None)


def _format_observations(entries: list[dict]) -> str:
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


def _parse_candidates(text: str) -> list[dict]:
    """Parse the model's JSON reply defensively: strip markdown fences and
    any leading/trailing prose, then validate shape."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    candidates = []
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        value = str(item.get("value", "")).strip()
        if not KEY_RE.match(key) or not value:
            continue
        facts = [str(f) for f in item.get("facts", []) if str(f).strip()]
        concepts = [str(c) for c in item.get("concepts", []) if str(c).strip()]
        candidates.append({"key": key, "value": value, "facts": facts, "concepts": concepts})
    return candidates


def distill(model: str | None = None, limit: int = 40) -> str:
    """Read recent observations, extract durable facts, and merge NEW keys
    into memory. Returns a human-readable summary."""
    entries = observations.read(limit)
    if not entries:
        return "No tool activity recorded yet — nothing to learn from. Use the assistant a bit first."

    model = model or _pick_model()
    if model is None:
        return "No Ollama model available for distillation."

    activity = _format_observations(entries)
    try:
        response = _local.chat(
            model=model,
            messages=[
                {"role": "system", "content": DISTILL_SYSTEM_PROMPT},
                {"role": "user", "content": f"Recent assistant activity:\n\n{activity}"},
            ],
            # Bounded on purpose: this call parses JSON, so an unbounded generation
            # (the OpenAI SDK default is 600s, and a thinking model can spend most of
            # its budget on the trace) truncates the array and _parse_facts returns
            # [] — a silent "nothing to learn" from a call that actually ran.
            max_tokens=2000,
            temperature=0.1,
        )
        reply = response["message"].get("content", "")
    except Exception as e:
        return f"Distillation failed (Ollama error: {e})."

    candidates = _parse_candidates(reply)
    if not candidates:
        return "The model returned no valid facts to learn."

    added, skipped = [], []
    for c in candidates:
        if memory.get_entry(c["key"]) is not None:
            skipped.append(c["key"])
            continue
        memory.remember(c["key"], c["value"], c["facts"], c["concepts"])
        added.append(c["key"])
        if len(added) >= MAX_NEW_KEYS:
            break

    parts = []
    if added:
        parts.append("Learned " + ", ".join(f"'{k}'" for k in added))
    if skipped:
        parts.append("skipped (already known): " + ", ".join(skipped))
    if not added and not skipped:
        return "Nothing new to learn — all extracted facts already in memory."
    return "🧠 " + "; ".join(parts) + "."
