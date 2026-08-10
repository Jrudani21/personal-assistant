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

def test_pick_model_routes_arithmetic_to_qwen3_coder():
    # ARITHMETIC is the only row in ROUTING that points to the 30B model.
    # This is the one measured result, so the test pins it explicitly.
    assert ROUTING[ARITHMETIC] == "deepseek-r1:7b"
    assert pick_model(TaskHint(role="Quant", text="compute 12 * 7")) == "deepseek-r1:7b"


def test_pick_model_routes_prose_numeric_to_qwen2_5_7b():
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


def test_pick_model_routes_code_to_coder_7b():
    assert pick_model(TaskHint(role="Fetcher", text="write a function that parses CSV")) == "deepseek-r1:7b"


def test_pick_model_routes_plain_prose_to_qwen2_5_7b():
    assert pick_model(TaskHint(role="Reporter", text="Summarize the findings.")) == "deepseek-r1:7b"


def test_pick_model_routes_empty_text_to_tool_io_model():
    # Defensive: an empty/unknown shape never raises; it falls through.
    # Empty text classifies as TOOL_IO, which routes to the cheapest model.
    assert classify_shape("") == TOOL_IO
    assert pick_model(TaskHint(role="Fetcher", text="")) == ROUTING[TOOL_IO]


def test_pick_model_uses_explicit_default_override():
    custom = "deepseek-r1:14b"
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


def test_routing_table_does_not_reference_deleted_models():
    # The 2026-08-10 cleanup removed qwen2.5:7b, qwen2.5-coder:7b, and
    # qwen3-coder:30b. No shape may point at a model that no longer exists.
    assert "qwen2.5:7b" not in ROUTING.values()
    assert "qwen2.5-coder:7b" not in ROUTING.values()
    assert "qwen3-coder:30b" not in ROUTING.values()
