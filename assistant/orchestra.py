"""MoE agent orchestra: per-role model chains with automatic failover.

Each agent role has an ordered fallback chain — cheapest/best → fallback →
local guarantee. A single call site resolves the chain to the first available
LLM; CrewAI agents just get the resolved wrapper.

Inspired by Mixture-of-Experts routing: dispatch to the specialist, escalate
on failure, never silently degrade.

Provider keys (env):
  DEEPSEEK_API_KEY  — DeepSeek API (paid, OpenAI-compatible)
  GOOGLE_API_KEY    — Gemini free tier (no credit card, 1500 req/day)
  (or GEMINI_API_KEY as alias)
"""

import os
from crewai.llm import LLM

DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GM_KEY = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")   # free tier drop-in (Fable 5 expiry backup)
SN_KEY = os.environ.get("SAMBANOVA_API_KEY", "")    # SambaNova free (UUID key, 200K tok/day)

DS_URL = "https://api.deepseek.com"
GM_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
OR_URL = "https://openrouter.ai/api/v1"
SN_URL = "https://api.sambanova.ai/v1"
# Local backend = LM Studio's OpenAI-compatible server. It replaced Ollama, which
# was uninstalled 2026-09-01: every chain's guaranteed-local tail used to point at
# http://localhost:11434, i.e. at nothing (curl -> 000), so the "always completes,
# never silently degrades" guarantee did not actually exist.
#
# The trailing "/v1" is REQUIRED and must stay. On this OpenAI-compatible path
# CrewAI hands base_url straight to the OpenAI client and appends nothing, so a
# bare host:port 404s — and the SDK's failure to parse that body surfaces as
# "TypeError: 'NoneType' object is not subscriptable", which looks nothing like a
# connection problem. Measured both ways: with /v1 -> "pong"; without -> TypeError.
OL_URL = "http://127.0.0.1:1234/v1"
# LM Studio ignores this key's value; litellm refuses an empty one.
LOCAL_API_KEY = "lm-studio"
# The one chat model this rig serves (RTX 4060 8GB -> a single model in VRAM),
# reached over the OpenAI-compatible path so it carries no "ollama/"-style prefix.
# It is a THINKING model: the trace lands in reasoning_content and content can come
# back empty, so prompts to it want a trailing " /no_think".
LOCAL_MODEL = "qwen/qwen3.5-9b"

# ── Fallback chains per agent role ──────────────────────────────────────
# Each entry: (condition, model, base_url)
# condition: truthy = try this entry. Last entry is always True (guaranteed local).
# Order: DeepSeek (primary) → OpenRouter free → SambaNova free → Gemini free
# → local LM Studio. All free tiers verified 2026-08-13:
#   OpenRouter  nvidia/nemotron-3-super-120b-a12b:free (reasoning, WORKS)
#   SambaNova   DeepSeek-V3.2 / gpt-oss-120b (free, sometimes rate-limited)
#   Gemini      gemini-3-flash-preview (free 1500/day)
# deepseek/deepseek-r1:free is GONE from OpenRouter's free list — use nemotron.
# "openai/" prefix is added for every endpoint, the local one included, so CrewAI
# treats them all as OpenAI-compatible API calls against base_url.

OR_FREE = "nvidia/nemotron-3-super-120b-a12b:free"
SN_FREE = "DeepSeek-V3.2"

CHAINS = {
    "fetcher": [
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (True,    LOCAL_MODEL,                               OL_URL),
    ],
    "quant": [
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (True,    LOCAL_MODEL,                               OL_URL),
    ],
    "analyst": [
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (True,    LOCAL_MODEL,                               OL_URL),
    ],
    "reporter": [
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (True,    LOCAL_MODEL,                               OL_URL),
    ],
}


def resolve(role: str) -> LLM:
    """Walk the chain for `role` and return the first available LLM.

    The last entry in every chain is a guaranteed local model (LM Studio), so
    this always returns something as long as the local server is up.
    """
    chain = CHAINS.get(role)
    if not chain:
        return LLM(model=f"openai/{LOCAL_MODEL}", base_url=OL_URL,
                   api_key=LOCAL_API_KEY)

    for ok, model, base_url in chain:
        if not ok:
            continue
        # "openai/" prefix routes the call through CrewAI/litellm as an
        # OpenAI-compatible request against base_url (else litellm derives the
        # provider from the model string and ignores base_url / fails auth).
        # This now applies to EVERY entry, the local one included: the local
        # backend is LM Studio's OpenAI-compatible server, not Ollama, so it needs
        # both the prefix and a key. It previously skipped both, which is why the
        # "guaranteed local" tail could not actually have worked.
        full_model = model if model.startswith("openai/") else f"openai/{model}"
        extra: dict = {}
        if base_url == OL_URL:
            extra["api_key"] = LOCAL_API_KEY
        elif ok is not True:  # a real cloud API key
            extra["api_key"] = ok
        return LLM(model=full_model, base_url=base_url, **extra)

    # Unreachable for the shipped chains (their last entry is always truthy). A
    # caller-supplied key-less chain used to get an LLM with NO api_key here, which
    # litellm then satisfies from ambient environment credentials — or fails at call
    # time, far away from the cause. Fail at the source instead.
    raise RuntimeError(f"no usable entry in the chain for role {role!r}")


def status() -> str:
    """Human-readable routing table showing what's active."""
    lines = ["MoE Orchestra — per-role routing:"]
    for role, chain in CHAINS.items():
        resolved = resolve(role)
        lines.append(f"  {role:10s} → {resolved.model}")
        for ok, model, url in chain:
            mark = "✓" if ok else "✗"
            lines.append(f"    {mark} {model} ({url})")
    return "\n".join(lines)
