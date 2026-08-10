"""Hybrid CrewAI pipeline: cheap steps on local Ollama, the reasoning step on
Claude Code CLI headless mode (billed against an existing Claude Pro/Max
subscription, not a paid API key). $0 marginal cost either way.

Shared by the `deep_analysis` chat tool (assistant/tools.py) and the
standalone `crewai_demo.py` script at the project root.
"""
import json
import re
import shutil
import subprocess

from pydantic import PrivateAttr

from crewai import Agent, Crew, Process, Task
from crewai.llm import LLM, BaseLLM

from . import crew_cache
from . import crew_tools
from . import tools as _tools
from . import config as _config

OLLAMA_BASE_URL = "http://localhost:11434"
FAST_MODEL = "ollama/qwen2.5:7b"
# Local stand-in for the Claude Pro analysis step, used only when Claude is
# unavailable. Its value is availability, not quality — see the measurements
# below. Deliberately the same model as FAST_MODEL: qwen3-coder:30b was tried
# first on the assumption that bigger is better here, and measured worse.
#
# Correctly identifying which product had higher revenue per unit, given the
# figures pre-computed in the prompt (3 runs each, crowded Analyst prompt):
#   qwen2.5:7b        2/3        4-11s
#   qwen3-coder:30b   0/3        4-24s   (18 GB, spills off an 8 GB card)
#   llama3.1:8b       0/3        3-10s
#
# qwen3-coder is tuned for code: it was the *only* model to get the isolated
# arithmetic right 3/3, and still inverted the comparison every time once the
# same numbers sat among unrelated statistics prose. Bigger did not help;
# prose synthesis with distractors is the weakness, not arithmetic.
FALLBACK_REASONING_MODEL = "ollama/qwen2.5:7b"


_FAILURE_MARKERS = (
    "cannot proceed", "unable to access", "could not be accessed",
    "not accessible", "file not found", "could not access",
    "unavailable at the specified path", "no raw data could be",
)

# ---- numeric-consistency checking ---------------------------------------
#
# Measured failure (see brain/notes): the Quant stage computes figures
# correctly, then the Analyst/Reporter misstates which is higher when the
# numbers sit among unrelated prose — and the wrong claim survives into the
# final report wearing the authority of a verified calculation. Existing
# guardrails check for empty/failure output, not for a claim that
# contradicts the very figures it was handed. These helpers close that gap
# deterministically: find "X is higher than Y" claims in the text, and
# cross-check each against the Quant stage's verified figures.

_FIGURE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*){0,5})\s*[=:]\s*"
    r"(\$?\s?[\d][\d,]*(?:\.\d+)?)"
)

_COMPARATIVE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*){0,3})\s+"
    r"(?:is|are|has|had|yields?|generates?|produces?|sells?|earns?|returns?|reports?|gives?|shows?|comes?)\s+"
    r"(?:(?:a|an|about|roughly|approximately|around|slightly|significantly|much|far|marginally|notably|clearly)\s+){0,2}"
    r"(higher|greater|larger|bigger|more|lower|less|smaller|fewer)"
    r"(?:\s+[A-Za-z][A-Za-z0-9]*){0,4}?"
    r"\s+(?:than|vs\.?|versus|compared\s+to|compared\s+with)\s+"
    r"([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*){0,3})"
)

_HIGHER_DEGREES = {"higher", "greater", "larger", "bigger", "more"}


def _normalize_label(label: str) -> str:
    """'Product B' / 'product-b' / 'B' all key to the same figure."""
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _extract_figures(text: str) -> dict[str, dict]:
    """Pull 'label = number' / 'label: number' pairs from the Quant stage's
    output. Returns {normalized_label: {'label': original, 'value': float}} —
    the original label is kept so lookups can match "Product C" against a
    figure stored as "Product C revenue per unit"."""
    figures: dict[str, dict] = {}
    for m in _FIGURE_RE.finditer(text or ""):
        raw_label = m.group(1).strip()
        raw = m.group(2).replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            value = float(raw)
        except ValueError:
            continue
        figures[_normalize_label(raw_label)] = {"label": raw_label, "value": value}
    return figures


def _lookup_figure(figures: dict[str, dict], label: str) -> float | None:
    """Find the verified figure for a comparison entity. Exact normalized
    match first ("Product C" == "Product C"); otherwise match the entity's
    words as a consecutive token sequence inside a figure's longer label
    ("Product C" inside "Product C revenue per unit"). Ambiguous matches
    (e.g. "revenue per unit" appears in every product's line) return None so
    the claim is skipped rather than misjudged."""
    norm = _normalize_label(label)
    if norm in figures:
        return figures[norm]["value"]
    tokens = re.findall(r"[a-z0-9]+", label.lower())
    if not tokens:
        return None
    hits = []
    for entry in figures.values():
        key_tokens = re.findall(r"[a-z0-9]+", entry["label"].lower())
        for i in range(len(key_tokens) - len(tokens) + 1):
            if key_tokens[i:i + len(tokens)] == tokens:
                hits.append(entry["value"])
                break
    return hits[0] if len(hits) == 1 else None


def _extract_comparisons(text: str) -> list[tuple[str, str, str]]:
    """Return (entity_a, entity_b, degree) for each explicit
    'A is higher/lower than B' claim."""
    return [
        (m.group(1).strip(), m.group(3).strip(), m.group(2))
        for m in _COMPARATIVE_RE.finditer(text or "")
    ]


def check_numeric_consistency(analyst_text: str, quant_text: str) -> tuple[bool, str]:
    """Verify every 'X is higher/lower than Y' claim in analyst_text against
    the verified figures in quant_text. Returns (ok, message); message is
    non-empty only when a claim contradicts the figures, and names the exact
    correction needed so a retried model knows what to fix."""
    figures = _extract_figures(quant_text)
    if not figures:
        return True, ""  # nothing verified to check against

    problems = []
    for a, b, degree in _extract_comparisons(analyst_text):
        na, nb = _normalize_label(a), _normalize_label(b)
        if na == nb:
            continue
        va, vb = _lookup_figure(figures, a), _lookup_figure(figures, b)
        if va is None or vb is None:
            continue
        claimed_higher = degree in _HIGHER_DEGREES
        if (va == vb) or (claimed_higher != (va > vb)):
            problems.append((a, b, degree, va, vb))

    if not problems:
        return True, ""
    lines = [
        (
            f'Your claim "{a} is {degree} than {b}" contradicts the verified '
            f"figures: {a} = {va:g}, {b} = {vb:g}. State the correct direction."
        )
        for a, b, degree, va, vb in problems
    ]
    return False, "Numeric consistency check failed:\n" + "\n".join(lines)


def _capturing_guardrail(state: dict):
    """Wraps the Quant task's guardrail so its accepted output is recorded
    for the Analyst/Reporter numeric check. Tasks run sequentially, so by
    the time the Analyst starts, state['quant_output'] holds the figures."""
    base = _make_guardrail(min_length=5)

    def guardrail(task_output):
        ok, msg = base(task_output)
        if ok:
            state["quant_output"] = (task_output.raw or "").strip()
        return ok, msg

    return guardrail


def _make_numeric_guardrail(state: dict, min_length: int = 15):
    """Standard length/failure guardrail, plus the numeric-consistency check
    against the Quant stage's verified figures."""
    base = _make_guardrail(min_length=min_length)

    def guardrail(task_output):
        ok, msg = base(task_output)
        if not ok:
            return ok, msg
        quant_text = state.get("quant_output", "")
        if quant_text:
            ok2, msg2 = check_numeric_consistency(task_output.raw or "", quant_text)
            if not ok2:
                return False, msg2
        return True, task_output.raw or ""

    return guardrail


def _make_guardrail(min_length: int = 20, expected_snippet: str | None = None):
    """Task guardrail: rejects (forcing a CrewAI-managed retry) outputs
    that are empty/near-empty (a common symptom of a transient empty/failed
    local-LLM response getting passed downstream as if it were real work)
    or that claim failure instead of doing the task. When expected_snippet
    is given (only for the file-grounded fetch case, where the real content
    is known up front), also rejects output that doesn't actually reflect
    the real data it was handed."""

    def guardrail(task_output):
        text = (task_output.raw or "").strip()
        if len(text) < min_length:
            return False, (
                "Output is empty or too short -- likely a failed LLM/tool "
                "call rather than real work. Produce the actual requested "
                "output."
            )
        lowered = text.lower()
        for marker in _FAILURE_MARKERS:
            if marker in lowered:
                return False, (
                    f'Output claims failure ("{marker}") instead of '
                    "completing the task. All information needed was "
                    "already provided in the task description -- use it "
                    "directly rather than trying to re-fetch it."
                )
        if expected_snippet is not None and expected_snippet.lower() not in lowered:
            return False, (
                "Output doesn't reflect the actual data provided in the "
                "task description. Re-read the task description and report "
                "the real data given there, not invented data."
            )
        return True, text

    return guardrail


def _extract_workspace_file_content(raw_input: str) -> str | None:
    """If raw_input references an existing file under data/workspace/,
    read it directly rather than trusting the (unreliable, ~7B-model)
    Fetcher agent to remember to call read_file instead of guessing."""
    for token in raw_input.replace(",", " ").split():
        token = token.strip("'\"")
        if "/" not in token and "\\" not in token and "." not in token:
            continue
        content = _tools.read_file(token)
        if not content.startswith("File not found:") and not content.startswith("Error:"):
            return f"File: {token}\n{content}"
    return None


class ClaudeCodeLLM(BaseLLM):
    """Routes calls through the Claude Code CLI's headless mode instead of
    an API key. Quirks worked around here:
    - Multi-line prompts passed as an argv string get mangled by the
      Windows .cmd wrapper, so the prompt is piped via stdin instead.
    - Headless mode still runs as the full Claude Code agent and inherits
      this machine's global hooks/settings (e.g. the caveman-mode output
      hook), which corrupts plain-prose output. --settings disables the
      caveman plugin for just this call; --system-prompt drops the
      Claude Code persona in favor of a plain analysis-assistant prompt.
    """

    model: str = "claude-code-cli"
    fallback_model: str = FALLBACK_REASONING_MODEL
    used_fallback: bool = False
    last_error: str | None = None
    _timeout_s: int = PrivateAttr(default=180)
    _isolated_settings: str = PrivateAttr(
        default_factory=lambda: json.dumps({"enabledPlugins": {"caveman@caveman": False}})
    )

    def call(self, messages, tools=None, callbacks=None,
              available_functions=None, from_task=None, from_agent=None,
              response_model=None):
        if isinstance(messages, str):
            prompt = messages
        else:
            prompt = "\n\n".join(
                f"[{m.get('role', 'user')}] {m.get('content', '')}"
                for m in messages
            )
        try:
            return self._call_claude(prompt)
        except Exception as e:
            # Pro plan limits, a missing CLI, or a timeout would otherwise fail
            # the whole pipeline. Degrade to a local model instead — worse
            # analysis beats no analysis, and the caller is told which ran.
            self.last_error = str(e)
            self.used_fallback = True
            return self._call_local_fallback(prompt)

    def _call_claude(self, prompt: str) -> str:
        claude_cmd = shutil.which("claude.cmd") or shutil.which("claude")
        if claude_cmd is None:
            raise RuntimeError("claude CLI not found on PATH")
        result = subprocess.run(
            [
                claude_cmd, "-p",
                "--settings", self._isolated_settings,
                "--system-prompt",
                "You are a helpful analysis assistant. Answer the user's "
                "request directly and completely in plain prose. Do not "
                "act as a coding agent and do not use tools.",
            ],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=int(_config.get("crew_timeout_s", self._timeout_s)),
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude -p failed: {result.stderr.decode('utf-8', errors='replace').strip()}")
        output = result.stdout.decode("utf-8", errors="replace").strip()
        if not output:
            raise RuntimeError("claude -p returned empty output")
        return output

    def _call_local_fallback(self, prompt: str) -> str:
        fallback_model = _config.get("crew_fallback_model", FALLBACK_REASONING_MODEL)
        fallback = LLM(model=fallback_model, base_url=OLLAMA_BASE_URL)
        return fallback.call(prompt)

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 180_000


def build_crew(raw_input: str, reasoning_llm=None) -> Crew:
    fast_model = _config.get("crew_fast_model", FAST_MODEL)
    fast_llm = LLM(model=fast_model, base_url=OLLAMA_BASE_URL)
    if reasoning_llm is None:
        reasoning_llm = ClaudeCodeLLM(model="claude-code-cli")

    fetcher = Agent(
        role="Fetcher",
        goal="Gather concise, factual, grounded notes on the given input",
        backstory=(
            "A researcher who collects raw facts without opinion. Never "
            "guesses from memory when a tool can confirm the answer: reads "
            "local files/uploaded documents directly when the input "
            "references one, otherwise searches the web or Wikipedia."
        ),
        llm=fast_llm,
        tools=crew_tools.FETCH_TOOLS,
    )
    quant = Agent(
        role="Quant",
        goal="Verify and compute any numbers the fetched notes depend on",
        backstory=(
            "A statistician who never trusts an unverified number. Uses "
            "run_python/calculator to check arithmetic, compute stats, or "
            "derive figures instead of estimating them by eye. If nothing "
            "in the notes needs computation, says so briefly and stops."
        ),
        llm=fast_llm,
        tools=crew_tools.QUANT_TOOLS,
    )
    analyst = Agent(
        role="Analyst",
        goal="Identify the 3 most important takeaways from the fetched notes and computed figures",
        backstory="A statistician who distills raw notes and verified numbers into key findings.",
        llm=reasoning_llm,
    )
    reporter = Agent(
        role="Reporter",
        goal="Write a short, clear summary report from the analysis",
        backstory="A technical writer producing a final one-paragraph report.",
        llm=fast_llm,
    )

    # Shared mutable state for the numeric-consistency guardrail: the Quant
    # task's accepted output is recorded here, then the Analyst/Reporter
    # guardrails check their claims against it. Tasks run sequentially, so
    # the Quant finishes before the Analyst starts.
    state: dict = {}

    file_content = _extract_workspace_file_content(raw_input)
    if file_content is not None:
        fetch_description = (
            f"Task: {raw_input}\n\n"
            "The relevant file has already been read for you -- use its "
            "actual contents below, do not guess or invent data:\n\n"
            f"{file_content}"
        )
        # first non-header-ish line of the real content, used to verify the
        # Fetcher's output actually reflects it rather than inventing data
        body_lines = file_content.splitlines()[1:]
        snippet = next((ln.strip() for ln in body_lines if ln.strip()), None)
        fetch_guardrail = _make_guardrail(min_length=10, expected_snippet=snippet)
    else:
        fetch_description = (
            f"Gather concise, factual notes on: {raw_input}\n"
            "If this references an uploaded document, search it with your "
            "search_documents tool. Otherwise ground the notes with a web "
            "search and/or Wikipedia rather than relying purely on memory."
        )
        fetch_guardrail = _make_guardrail(min_length=20)
    fetch_task = Task(
        description=fetch_description,
        expected_output="A bullet list of raw facts, no analysis.",
        agent=fetcher,
        guardrail=fetch_guardrail,
        guardrail_max_retries=2,
    )
    quant_task = Task(
        description=(
            "Review the fetched notes. If they contain numbers, rates, "
            "totals, or claims that can be verified/derived computationally, "
            "use run_python or calculator to check or compute them. If "
            "nothing in the notes needs computation, state that plainly."
        ),
        expected_output="A short list of verified/computed figures, or a one-line note that none were needed.",
        agent=quant,
        context=[fetch_task],
        guardrail=_capturing_guardrail(state),
        guardrail_max_retries=2,
    )
    analyze_task = Task(
        description="Analyze the fetched notes and computed figures, and extract the 3 most important takeaways.",
        expected_output="A numbered list of exactly 3 takeaways.",
        agent=analyst,
        context=[fetch_task, quant_task],
        guardrail=_make_numeric_guardrail(state, min_length=15),
        guardrail_max_retries=2,
    )
    report_task = Task(
        description="Write a short final report (one paragraph) summarizing the analysis.",
        expected_output="A single clear paragraph.",
        agent=reporter,
        context=[analyze_task],
        guardrail=_make_numeric_guardrail(state, min_length=15),
        guardrail_max_retries=2,
    )

    return Crew(
        agents=[fetcher, quant, analyst, reporter],
        tasks=[fetch_task, quant_task, analyze_task, report_task],
        process=Process.sequential,
    )


def run_deep_analysis(raw_input: str) -> str:
    """Runs the fetch -> verify/compute -> analyze -> report crew on a
    topic, question, or file/document reference, and returns the final
    report text. Takes roughly 45-120s (tool calls plus the analysis step,
    which calls out to Claude Code CLI); identical re-runs within the cache
    TTL return the stored report instantly instead."""
    cached = crew_cache.get_cached(raw_input)
    if cached is not None:
        return cached + (
            "\n\n---\n_(Cached result — same topic was analyzed recently. "
            "Use the clear-cache tool or sidebar button to force a fresh run.)_"
        )

    reasoning_llm = ClaudeCodeLLM(model="claude-code-cli")
    try:
        result = str(build_crew(raw_input, reasoning_llm).kickoff())
    except Exception as e:
        return f"Deep analysis error: {e}"

    if reasoning_llm.used_fallback:
        # Say so plainly, and be specific about the failure mode rather than
        # vaguely "lower confidence": measured on this setup, local models
        # invert numeric comparisons in roughly a third of runs even when the
        # correct figures are handed to them in the prompt. Also deliberately
        # NOT cached — see crew_cache.store.
        return (
            f"{result}\n\n---\n_Note: the Claude analysis step was unavailable "
            f"({reasoning_llm.last_error}), so this was analyzed locally with "
            f"{reasoning_llm.fallback_model}. Local analysis misstates numeric "
            f"comparisons (which value is higher, by how much) in roughly 1 run "
            f"in 3 — verify any figures below against the source before relying "
            f"on them._"
        )
    crew_cache.store(raw_input, result, used_fallback=False)
    return result
