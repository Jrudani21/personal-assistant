"""Long-chat compaction: summarize the old middle of a conversation so it
fits Ollama's context window, without ever touching what's actually stored
in the chat (chat['messages'] stays full-fidelity for the UI, export, and
search). The cache lives in a separate chat['compaction'] field.

Design notes:
- Token estimate is chars/4 (no tokenizer dependency) — good enough for a
  trigger threshold, not meant to be exact.
- Only cuts at a 'user'-role message boundary, since a tool-call round
  (assistant-with-tool_calls + its tool results) always sits between two
  user turns in this app's message flow — cutting mid-round would send
  Ollama an orphaned tool result and error.
- The cut point snaps to buckets of BUCKET_SIZE messages instead of a
  precise "last N", so the cache is reused across many turns instead of
  re-summarizing on every single message once past the threshold.
"""
import ollama

from . import sessions

CONTEXT_TOKENS = 8192  # matches this machine's OLLAMA_CONTEXT_LENGTH
TRIGGER_TOKENS = 4000  # leave headroom for system prompt, tool schemas, response
KEEP_RECENT = 12  # messages kept verbatim in the tail
BUCKET_SIZE = 10

SUMMARY_PROMPT = (
    "Summarize the conversation so far into this exact structure:\n"
    "Goal: <what the user is trying to accomplish>\n"
    "Constraints: <preferences/limits stated>\n"
    "Progress: <done, in progress, blocked>\n"
    "Key decisions: <choices made and why>\n"
    "Next steps: <what's still open>\n\n"
    "Preserve exact file paths, function/variable names, and error messages "
    "verbatim — do not paraphrase those. Be terse everywhere else."
)


def _estimate_tokens(messages: list[dict]) -> int:
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return chars // 4


def _safe_cut_index(messages: list[dict], target: int) -> int:
    """Largest 'user'-role index <= target, so we never cut mid tool-round."""
    for i in range(min(target, len(messages) - 1), -1, -1):
        if messages[i]["role"] == "user":
            return i
    return 0


def _summarize(model: str, messages: list[dict]) -> str | None:
    convo = "\n".join(f"{m['role']}: {m.get('content', '')}" for m in messages if m.get("content"))
    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": convo},
            ],
        )
        return response["message"].get("content", "").strip() or None
    except Exception:
        return None  # summarization is best-effort; caller falls back to raw history


def get_llm_messages(chat: dict, model: str) -> list[dict]:
    """Returns what to send to the model for this turn: the full history
    verbatim if it's short enough, otherwise a cached/fresh summary of the
    old part plus the recent tail verbatim."""
    history = chat["messages"]
    if _estimate_tokens(history) <= TRIGGER_TOKENS or len(history) <= KEEP_RECENT:
        return history

    target = len(history) - KEEP_RECENT
    bucket_target = (target // BUCKET_SIZE) * BUCKET_SIZE
    cut = _safe_cut_index(history, bucket_target) or _safe_cut_index(history, target)

    cached = chat.get("compaction")
    if not (cached and cached.get("up_to_index") == cut):
        summary = _summarize(model, history[:cut])
        if summary is None:
            return history  # Ollama unreachable or summarization failed — just send raw history
        chat["compaction"] = {"up_to_index": cut, "summary": summary}
        sessions.save(chat)
        cached = chat["compaction"]

    summary_msg = {"role": "user", "content": f"[Summary of earlier conversation]\n{cached['summary']}"}
    return [summary_msg] + history[cut:]
