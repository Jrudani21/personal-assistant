"""Tests for the multi-model router.

Pure stdlib; does not require ollama, crewai, or any model to be running.
"""
import pytest

from assistant import router
from assistant.router import (
    ARITHMETIC,
    CODE,
    FAST_MODEL,
    PROSE,
    PROSE_NUMERIC,
    ROUTING,
    TOOL_IO,
    TaskHint,
    classify_shape,
    ollama_model,
    pick_model,
)


# ---------- classify_shape ----------

def test_classify_empty_text_as_tool_io():
    assert classify_shape("") == TOOL_IO


def test_classify_whitespace_only_as_tool_io():
    assert classify_shape("   \n\t  ") == TOOL_IO


def test_classify_isolated_arithmetic():
    # Naked numbers and an operator, no surrounding prose.
    assert classify_shape("3 + 4 = 7") == ARITHMETIC
    assert classify_shape("compute 12 * 7") == ARITHMETIC


def test_classify_arithmetic_via_verbs():
    # The verb forms in the regex: total, mean, rate, ratio, etc.
    assert classify_shape("what is the mean of the sample?") == ARITHMETIC
    assert classify_shape("compute the variance") == ARITHMETIC


def test_classify_prose_numeric_when_comparison_claim_appears():
    # The exact failure mode from the smoke test: a comparison verb
    # with two numbers nearby, embedded in prose.
    text = (
        "Product A sells for $50/unit and B for $25/unit. Among the four "
        "unrelated statistics facts in this report, A is higher than B."
    )
    assert classify_shape(text) == PROSE_NUMERIC


def test_classify_arithmetic_wins_over_prose_for_plain_calculation():
    # Computation with no comparison claim should still route as ARITHMETIC.
    text = "Calculate 1200 * 0.08 to get the rate."
    assert classify_shape(text) == ARITHMETIC


def test_classify_code_when_writing_function():
    text = "Write a function that returns the sum of a list."
    assert classify_shape(text) == CODE


def test_classify_code_wins_over_arithmetic_in_prompt():
    # A prompt that contains both code intent AND math should still be CODE.
    text = "Write a function that computes the variance of a list."
    assert classify_shape(text) == CODE


def test_classify_plain_prose():
    text = "Compare Poisson regression and SARIMA models in plain English."
    assert classify_shape(text) == PROSE


def test_classify_prose_with_dollar_amounts_but_no_comparison_verb():
    # Mentions figures but does not compare them — PROSE, not PROSE_NUMERIC.
    text = "The report references $50 in product A and $25 in product B."
    assert classify_shape(text) == PROSE


def test_classify_is_case_insensitive():
    assert classify_shape("WRITE A FUNCTION") == CODE
    assert classify_shape("IS HIGHER THAN $50") in (PROSE_NUMERIC, ARITHMETIC)


# ---------- TaskHint validation ----------

def test_taskhint_rejects_empty_role():
    with pytest.raises(ValueError, match="role"):
        TaskHint(role="", text="something")


def test_taskhint_rejects_none_text():
    # None is not allowed; empty string IS allowed (classify_shape
    # routes it to TOOL_IO so the pipeline keeps running).
    with pytest.raises(ValueError, match="text"):
        TaskHint(role="Fetcher", text=None)


def test_taskhint_accepts_whitespace_only_text():
    # Whitespace falls through to TOOL_IO; the pipeline stays alive.
    hint = TaskHint(role="Fetcher", text="   \n  ")
    assert classify_shape(hint.text) == TOOL_IO


# ---------- pick_model ----------

# The router maps each task shape to a model that's actually installed.
# Current lineup: deepseek-r1:7b (default/reasoning), deepseek-r1:1.5b
# (cheap tool-io), qwen3:8b (tool calling). The old qwen2.5*/qwen3-coder
# models were removed from Ollama, so tests pin the intent, not names.

def test_pick_model_routes_arithmetic_to_deepseek_r1():
    assert pick_model(TaskHint(role="Quant", text="compute 12 * 7")) == "deepseek-r1:7b"


def test_pick_model_routes_prose_numeric_to_deepseek_r1():
    # The smoke-test result: the 7B beats the 30B on this shape.
    text = (
        "Product A sells for $50/unit and B for $25/unit. "
        "A is higher than B."
    )
    assert pick_model(TaskHint(role="Analyst", text=text)) == "deepseek-r1:7b"


def test_classify_prose_numeric_with_figures_stated_first():
    # The exact failure-mode structure: figures stated up front, the
    # comparison claim comes later with distractor prose between them.
    text = (
        "Given B = $50/unit and C = $25/unit, B and C each sold 100 units. "
        "We also note that the mean age of the sample is 42, the median is "
        "38, and the standard deviation is 11. Among the products, B is "
        "higher than C in revenue per unit."
    )
    assert classify_shape(text) == PROSE_NUMERIC


def test_pick_model_routes_code_to_deepseek_r1():
    assert pick_model(TaskHint(role="Fetcher", text="write a function that parses CSV")) == "deepseek-r1:7b"


def test_pick_model_routes_plain_prose_to_deepseek_r1():
    assert pick_model(TaskHint(role="Reporter", text="Summarize the findings.")) == "deepseek-r1:7b"


def test_pick_model_routes_empty_text_to_tool_io():
    # Defensive: an empty shape never raises; it routes to the cheap TOOL_IO
    # model (deepseek-r1:1.5b), not the full reasoning default.
    assert pick_model(TaskHint(role="Fetcher", text="")) == ROUTING[TOOL_IO]


def test_pick_model_routes_unknown_shape_to_default():
    # A shape the classifier doesn't recognize falls through to FAST_MODEL.
    assert pick_model(TaskHint(role="Fetcher", text="zzz no match here")) == FAST_MODEL


def test_pick_model_uses_explicit_default_override():
    custom = "qwen3:8b"
    result = pick_model(TaskHint(role="Fetcher", text=""), default=custom)
    assert result == custom


def test_pick_model_does_not_call_ollama():
    # The classifier must be deterministic and offline. If this test ever
    # requires network, the router has regressed.
    import socket
    with socket.socket() as s:
        s.settimeout(0.0)  # non-blocking: would fail loudly if anything tried to connect
    pick_model(TaskHint(role="Quant", text="compute 3+4"))


# ---------- ollama_model ----------

def test_ollama_model_adds_prefix_once():
    assert ollama_model("deepseek-r1:7b") == "ollama/deepseek-r1:7b"


def test_ollama_model_idempotent():
    assert ollama_model("ollama/deepseek-r1:7b") == "ollama/deepseek-r1:7b"


# ---------- routing table integrity ----------

def test_routing_table_has_no_duplicate_models_per_shape():
    # Sanity: each shape must map to exactly one model.
    assert len(set(ROUTING.values())) <= len(ROUTING)


def test_routing_table_covers_all_known_shapes():
    expected = {ARITHMETIC, PROSE_NUMERIC, PROSE, CODE, TOOL_IO}
    assert set(ROUTING) >= expected


def test_routing_table_references_installed_models_only():
    # Every model in the routing table must be one that's actually installed.
    # If this fails, someone pointed the router at a removed model.
    installed = {"deepseek-r1:7b", "deepseek-r1:1.5b", "deepseek-r1:14b", "qwen3:8b"}
    for model in set(ROUTING.values()) | {FAST_MODEL}:
        assert model in installed, f"router references missing model: {model}"


def test_routing_table_does_not_reference_removed_qwen_models():
    # The old qwen2.5* / qwen3-coder models were removed from Ollama.
    # The router must not silently point at a model that no longer exists.
    removed = {"qwen2.5:7b", "qwen2.5-coder:7b", "qwen3-coder:30b", "qwen3-coder:14b"}
    for model in set(ROUTING.values()) | {FAST_MODEL}:
        assert model not in removed, f"router references removed model: {model}"
