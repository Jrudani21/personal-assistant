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
OL_URL = "http://localhost:11434"

# ── Fallback chains per agent role ──────────────────────────────────────
# Each entry: (condition, model, base_url)
# condition: truthy = try this entry. Last entry is always True (guaranteed local).
# Order: DeepSeek (primary) → OpenRouter free → SambaNova free → Gemini free
# → local Ollama. All free tiers verified 2026-08-13:
#   OpenRouter  nvidia/nemotron-3-super-120b-a12b:free (reasoning, WORKS)
#   SambaNova   DeepSeek-V3.2 / gpt-oss-120b (free, sometimes rate-limited)
#   Gemini      gemini-3-flash-preview (free 1500/day)
# deepseek/deepseek-r1:free is GONE from OpenRouter's free list — use nemotron.
# "openai/" prefix is added for non-Ollama URLs so CrewAI treats them as API calls.

OR_FREE = "nvidia/nemotron-3-super-120b-a12b:free"
SN_FREE = "DeepSeek-V3.2"

CHAINS = {
    "fetcher": [
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (True,    "ollama/deepseek-r1-tool-calling:7b",      OL_URL),
    ],
    "quant": [
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (True,    "ollama/deepseek-r1-tool-calling:7b",      OL_URL),
    ],
    "analyst": [
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (True,    "ollama/deepseek-r1:14b",                  OL_URL),
    ],
    "reporter": [
        (DS_KEY, "deepseek-chat",                            DS_URL),
        (OR_KEY, OR_FREE,                                     OR_URL),
        (SN_KEY, SN_FREE,                                     SN_URL),
        (GM_KEY, "gemini-3-flash-preview",                   GM_URL),
        (True,    "ollama/deepseek-r1:8b",                   OL_URL),
    ],
}


def resolve(role: str) -> LLM:
    """Walk the chain for `role` and return the first available LLM.

    The last entry in every chain is a guaranteed local Ollama model,
    so this always returns something if Ollama is running.
    """
    chain = CHAINS.get(role)
    if not chain:
        return LLM(model="ollama/deepseek-r1:7b", base_url=OL_URL)

    for ok, model, base_url in chain:
        if not ok:
            continue
        prefix = "openai/" if base_url in (DS_URL, GM_URL) else ""
        full_model = f"{prefix}{model}"
        extra = {}
        if base_url in (DS_URL, GM_URL) and ok is not True:
            extra["api_key"] = ok
        return LLM(model=full_model, base_url=base_url, **extra)

    return LLM(model=chain[-1][1], base_url=chain[-1][2])


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
