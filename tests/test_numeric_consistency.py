"""Tests for the numeric-consistency guardrail: the Analyst/Reporter's
'X is higher than Y' claims are checked deterministically against the
Quant stage's verified figures (the failure mode measured and documented
in brain/notes — local models invert numeric comparisons under prompt
crowding)."""
from assistant.crew import (
    _extract_comparisons,
    _extract_figures,
    _lookup_figure,
    _make_numeric_guardrail,
    check_numeric_consistency,
)
from tests.conftest import FakeTaskOutput

# The exact shape from the original failure: Quant computes revenue per unit
# correctly, Analyst then claims the wrong product is higher.
QUANT_OUTPUT = (
    "Verified figures:\n"
    "- Product A revenue per unit: $30\n"
    "- Product B revenue per unit: $50\n"
    "- Product C revenue per unit: $25\n"
)


# ---------- _extract_figures ----------

def test_extract_figures_parses_labeled_numbers():
    fig = _extract_figures(QUANT_OUTPUT)
    assert fig["productarevenueperunit"]["value"] == 30
    assert fig["productbrevenueperunit"]["value"] == 50
    assert fig["productcrevenueperunit"]["value"] == 25


def test_extract_figures_handles_equals_sign_and_dollar_commas():
    fig = _extract_figures("Total revenue = $1,250.50; margin: 18%")
    assert fig["totalrevenue"]["value"] == 1250.50
    assert fig["margin"]["value"] == 18


def test_extract_figures_returns_empty_for_no_numbers():
    assert _extract_figures("No figures here, just prose.") == {}


def test_short_entity_matches_long_figure_label():
    # "Product C" must match a figure stored as "Product C revenue per unit"
    fig = _extract_figures(QUANT_OUTPUT)
    assert _lookup_figure(fig, "Product C") == 25
    assert _lookup_figure(fig, "C") == 25
    assert _lookup_figure(fig, "product-b") == 50


def test_ambiguous_entity_matching_multiple_figures_returns_none():
    fig = _extract_figures(QUANT_OUTPUT)
    # "revenue per unit" is a token sequence in all three figure labels
    assert _lookup_figure(fig, "revenue per unit") is None


# ---------- _extract_comparisons ----------

def test_extract_comparisons_finds_plain_claim():
    comps = _extract_comparisons("Product B has higher revenue per unit than Product C.")
    assert ("Product B", "Product C", "higher") in comps


def test_extract_comparisons_finds_lower_claim():
    comps = _extract_comparisons("Model A is less accurate than Model B.")
    assert ("Model A", "Model B", "less") in comps


def test_extract_comparisons_handles_modifier_words():
    comps = _extract_comparisons("B is significantly higher than C.")
    assert ("B", "C", "higher") in comps


def test_temporal_comparison_parses_but_is_ignored_without_figures():
    # "Revenue is higher this year than last year" parses as a comparison,
    # but neither "revenue" nor "last year" has a verified figure in the
    # quant output, so the guardrail ignores it rather than failing.
    comps = _extract_comparisons("Revenue is higher this year than last year.")
    assert len(comps) == 1
    ok, _ = check_numeric_consistency(
        "Revenue is higher this year than last year.", QUANT_OUTPUT
    )
    assert ok is True


# ---------- check_numeric_consistency ----------

def test_correct_direction_passes():
    analyst = "Takeaway: Product B has higher revenue per unit than Product C ($50 vs $25)."
    ok, msg = check_numeric_consistency(analyst, QUANT_OUTPUT)
    assert ok is True
    assert msg == ""


def test_inverted_direction_fails():
    analyst = "Takeaway: Product C has higher revenue per unit than Product B."
    ok, msg = check_numeric_consistency(analyst, QUANT_OUTPUT)
    assert ok is False
    assert "Product C" in msg and "Product B" in msg
    assert "50" in msg and "25" in msg  # names the verified figures


def test_multiple_violations_all_reported():
    analyst = (
        "A is higher than B. "
        "C is higher than B. "
        "B is lower than A."
    )
    ok, msg = check_numeric_consistency(analyst, QUANT_OUTPUT)
    assert ok is False
    assert msg.count("contradicts the verified figures") == 3


def test_claim_about_unverified_entity_is_ignored():
    analyst = "Product Z has higher revenue than Product B."
    ok, _ = check_numeric_consistency(analyst, QUANT_OUTPUT)
    assert ok is True  # Z has no verified figure — nothing to check


def test_no_figures_in_quant_output_passes_anything():
    ok, _ = check_numeric_consistency("B is higher than C.", "No numbers needed here.")
    assert ok is True


def test_equal_figures_reject_any_direction():
    quant = "Product A revenue: 100\nProduct B revenue: 100\n"
    assert check_numeric_consistency("A is higher than B.", quant)[0] is False
    assert check_numeric_consistency("A is lower than B.", quant)[0] is False


def test_lower_direction_matches_figures():
    analyst = "Product C is lower than Product B."
    ok, _ = check_numeric_consistency(analyst, QUANT_OUTPUT)
    assert ok is True


# ---------- guardrail wiring ----------

def test_numeric_guardrail_rejects_inverted_claim():
    state = {"quant_output": QUANT_OUTPUT}
    guardrail = _make_numeric_guardrail(state, min_length=5)
    ok, msg = guardrail(FakeTaskOutput(
        "Product C has higher revenue per unit than Product B."
    ))
    assert ok is False
    assert "contradicts the verified figures" in msg


def test_numeric_guardrail_passes_consistent_claim():
    state = {"quant_output": QUANT_OUTPUT}
    guardrail = _make_numeric_guardrail(state, min_length=5)
    ok, _ = guardrail(FakeTaskOutput(
        "Product B has higher revenue per unit than Product C."
    ))
    assert ok is True


def test_numeric_guardrail_without_quant_output_is_plain_length_check():
    guardrail = _make_numeric_guardrail({}, min_length=10)
    ok, _ = guardrail(FakeTaskOutput("A sufficiently long claim about B being higher than C."))
    assert ok is True
    ok, _ = guardrail(FakeTaskOutput("short"))
    assert ok is False
