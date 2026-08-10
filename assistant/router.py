"""Pick the local model for a CrewAI stage based on the task shape.

The router is a static function — no LLM in the classification loop itself. The
mapping is grounded in the smoke test in
``notes/Local models invert numeric comparisons under prompt crowding.md`` for
arithmetic-under-crowding (qwen2.5:7b beats qwen3-coder:30b), and a hypothesis
for every other shape — see the open-questions list in the project note before
treating the unmarked rows as load-bearing.

The router is intentionally small. Adding an LLM here would reintroduce the
exact class of failure (silent self-routing) that the guardrails are designed
to catch elsewhere in the pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# --- task shapes -----------------------------------------------------------

ARITHMETIC = "arithmetic"            # isolated numeric computation
PROSE_NUMERIC = "prose_numeric"      # prose synthesis that quotes figures
PROSE = "prose"                      # plain prose, no numbers to get wrong
CODE = "code"                        # code generation / review
TOOL_IO = "tool_io"                  # pure tool/file usage, no real LLM reasoning


# --- model table -----------------------------------------------------------
# The CrewAI LLM() constructor takes ``ollama/<name>``; we strip the prefix
# here so the rest of the assistant code stays unchanged.
#
# HYPOTHESIS rows are smoke-test candidates. ``measured`` rows are grounded in
# a known result — changing them requires re-running the smoke test.

FAST_MODEL = "deepseek-r1:7b"          # cheap, fast, default for everything vague

ROUTING: dict[str, str] = {
    # shape          -> model                     status
    ARITHMETIC:      "deepseek-r1:7b",            # measured: 3/3 on isolated arithmetic
    PROSE_NUMERIC:   "deepseek-r1:7b",            # measured: 2/3 vs 0/3 for the 30B
    PROSE:           "deepseek-r1:7b",            # HYPOTHESIS: prose synthesis default
    CODE:            "deepseek-r1:7b",            # HYPOTHESIS: coder-specialized
    TOOL_IO:         "deepseek-r1:1.5b",          # HYPOTHESIS: cheapest sufficient
}


# --- classifier ------------------------------------------------------------

# A comparison claim — "X is higher than Y", "A exceeds B", "more than the
# other", etc. This is the failure mode the smoke test surfaced: the model
# sees the figures (stated earlier in the prompt), then makes a comparison
# claim that inverts them. Matching the claim is what lets us route to a
# model that handled that case better.
_COMPARISON_VERB_RE = re.compile(
    r"\b(higher|lower|more|less|greater|smaller|bigger|larger|exceeds|exceeded|"
    r"surpasses|outperforms|underperforms|outpaced|outstripped)\b",
    re.IGNORECASE,
)

# A "quoted figure" — a dollar amount, a percent, or a plain number.
# Crude on purpose: we only need to know the text contains numbers that
# could be compared.
_QUOTED_FIGURE_RE = re.compile(r"\$\d|\d+\s*%|\b\d{2,}\b")

# Naked arithmetic: digits, operators, units, ratio/percentage verbs.
_ARITHMETIC_RE = re.compile(
    r"""
    (?:
        \d\s*[+\-*/^%]\s*\d         # 3 + 4, 12*7
        |
        \b(sum|total|mean|average|median|std|stddev|variance|min|max|rate|ratio|percentage|proportion|count)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Code-flavoured keywords. Conservative — only matches when the prompt is
# clearly asking the model to write or review code rather than describe it.
_CODE_RE = re.compile(
    r"\b(write\s+(?:a\s+)?(?:function|class|script|query|regex|sql|python)|"
    r"implement|refactor|debug|fix\s+the\s+bug|review\s+the\s+code|"
    r"```[a-zA-Z]+\s*$)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskHint:
    """The minimum information needed to route a stage.

    ``role`` is one of the four CrewAI stage names (Fetcher/Quant/Analyst/
    Reporter). ``text`` is the parts of the prompt that are unique to this
    run — the stage template's boilerplate should be excluded so the
    classifier sees signal, not scaffolding.
    """
    role: str
    text: str

    def __post_init__(self) -> None:
        if not self.role:
            raise ValueError("TaskHint.role is required")
        # text may be whitespace-only; classify_shape handles that as TOOL_IO.
        if self.text is None:
            raise ValueError("TaskHint.text is required")


def _is_prose_with_numbers(text: str) -> bool:
    """A comparison claim and at least one quoted figure, anywhere in the
    text. The two don't have to be adjacent — the smoke test failure
    mode is the figures being stated up front and the claim coming
    later, with distractor prose in between."""
    return bool(_COMPARISON_VERB_RE.search(text) and _QUOTED_FIGURE_RE.search(text))


def classify_shape(text: str) -> str:
    """Map a stage prompt to a task shape. Order matters: the more specific
    patterns win over the more general ones.

    Precedence:
      1. CODE      — explicit code-generation intent
      2. ARITHMETIC — naked computation, no surrounding prose
      3. PROSE_NUMERIC — prose that compares quoted numbers (the failure mode)
      4. PROSE      — anything else with text
      5. TOOL_IO    — empty / whitespace-only text
    """
    if not text or not text.strip():
        return TOOL_IO

    if _CODE_RE.search(text):
        return CODE

    if _ARITHMETIC_RE.search(text):
        # If the arithmetic sits among prose that ALSO makes a numeric
        # comparison claim, prefer the model that handled that case better.
        if _is_prose_with_numbers(text):
            return PROSE_NUMERIC
        return ARITHMETIC

    if _is_prose_with_numbers(text):
        return PROSE_NUMERIC

    return PROSE


def pick_model(hint: TaskHint, default: str | None = None) -> str:
    """Return the local model name (without the ``ollama/`` prefix) that
    should serve ``hint``.

    The ``default`` parameter, when given, overrides the routing table for
    any shape — useful for tests and for one-off calls where the caller
    already knows what model it wants. When ``default`` is None, the
    routing table is consulted and an unknown shape falls through to
    ``FAST_MODEL``.
    """
    if default is not None:
        return default
    shape = classify_shape(hint.text)
    return ROUTING.get(shape, FAST_MODEL)


def ollama_model(name: str) -> str:
    """Wrap a bare model name the way crewai.LLM wants it."""
    if name.startswith("ollama/"):
        return name
    return f"ollama/{name}"
