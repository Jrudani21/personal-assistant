import json
import subprocess

import pytest

from assistant import crew
from tests.conftest import FakeTaskOutput


@pytest.fixture(autouse=True)
def _no_crew_cache(monkeypatch):
    """Keep the on-disk deep-analysis cache out of unit tests: always miss on
    read, and drop whatever a test wrote. Cache behavior has its own test
    module (test_crew_cache.py)."""
    monkeypatch.setattr(crew.crew_cache, "get_cached", lambda *a, **k: None)
    yield
    crew.crew_cache.clear()


# ---------- _extract_workspace_file_content ----------

def test_extract_file_content_finds_existing_file(tmp_workspace_file):
    path, content = tmp_workspace_file
    result = crew._extract_workspace_file_content(f"analyze {path.name}")
    assert result is not None
    assert content.splitlines()[0] in result


def test_extract_file_content_missing_file_returns_none():
    assert crew._extract_workspace_file_content("analyze totally_nonexistent_xyz.csv") is None


def test_extract_file_content_ignores_plain_topic_words():
    assert crew._extract_workspace_file_content("compare Poisson regression and SARIMA models") is None


def test_extract_file_content_strips_quotes_and_commas(tmp_workspace_file):
    path, _ = tmp_workspace_file
    result = crew._extract_workspace_file_content(f"analyze '{path.name}', please")
    assert result is not None
    assert path.name in result


def test_extract_file_content_prefixes_with_filename(tmp_workspace_file):
    path, _ = tmp_workspace_file
    result = crew._extract_workspace_file_content(f"look at {path.name}")
    assert result.startswith(f"File: {path.name}")


# ---------- _make_guardrail ----------

def test_guardrail_rejects_empty_output():
    guardrail = crew._make_guardrail(min_length=20)
    ok, _ = guardrail(FakeTaskOutput(""))
    assert ok is False


def test_guardrail_rejects_none_raw():
    guardrail = crew._make_guardrail(min_length=1)
    ok, _ = guardrail(FakeTaskOutput(None))
    assert ok is False


def test_guardrail_rejects_too_short_output():
    guardrail = crew._make_guardrail(min_length=20)
    ok, _ = guardrail(FakeTaskOutput("short"))
    assert ok is False


def test_guardrail_accepts_sufficient_clean_output():
    guardrail = crew._make_guardrail(min_length=5)
    text = "This is a proper, sufficiently long factual summary of the data."
    ok, returned = guardrail(FakeTaskOutput(text))
    assert ok is True
    assert returned == text


@pytest.mark.parametrize("marker", crew._FAILURE_MARKERS)
def test_guardrail_rejects_every_known_failure_marker(marker):
    guardrail = crew._make_guardrail(min_length=1)
    text = f"Unfortunately, the analysis {marker} given the current constraints and padding text."
    ok, msg = guardrail(FakeTaskOutput(text))
    assert ok is False
    assert marker in msg.lower()


def test_guardrail_marker_detection_is_case_insensitive():
    guardrail = crew._make_guardrail(min_length=1)
    text = "The FILE NOT FOUND at the given path, so nothing could be reported here."
    ok, _ = guardrail(FakeTaskOutput(text))
    assert ok is False


def test_guardrail_with_expected_snippet_present_passes():
    guardrail = crew._make_guardrail(min_length=5, expected_snippet="A,120,3600")
    text = "The raw data includes the row A,120,3600 among the other products listed."
    ok, _ = guardrail(FakeTaskOutput(text))
    assert ok is True


def test_guardrail_with_expected_snippet_missing_fails():
    guardrail = crew._make_guardrail(min_length=5, expected_snippet="A,120,3600")
    text = "The data shows entirely different invented numbers not from the source."
    ok, msg = guardrail(FakeTaskOutput(text))
    assert ok is False
    assert "actual data" in msg.lower() or "invented" in msg.lower()


def test_guardrail_expected_snippet_check_is_case_insensitive():
    guardrail = crew._make_guardrail(min_length=5, expected_snippet="Revenue Total")
    text = "The report references the revenue total figure explicitly here."
    ok, _ = guardrail(FakeTaskOutput(text))
    assert ok is True


# ---------- build_crew wiring ----------

def test_build_crew_agent_and_task_order():
    c = crew.build_crew("compare Poisson regression and SARIMA")
    assert [t.agent.role for t in c.tasks] == ["Fetcher", "Quant", "Analyst", "Reporter"]


def test_build_crew_all_tasks_have_guardrails():
    c = crew.build_crew("compare Poisson regression and SARIMA")
    assert all(t.guardrail is not None for t in c.tasks)


def test_build_crew_fetch_guardrail_uses_real_file_snippet(tmp_workspace_file):
    path, _ = tmp_workspace_file
    c = crew.build_crew(f"analyze {path.name}")
    fetch_task = c.tasks[0]
    ok, _ = fetch_task.guardrail(FakeTaskOutput(
        "This output is long enough but contains entirely made-up figures."
    ))
    assert ok is False


def test_build_crew_fetch_guardrail_without_file_has_no_snippet_requirement():
    c = crew.build_crew("compare Poisson regression and SARIMA")
    fetch_task = c.tasks[0]
    ok, _ = fetch_task.guardrail(FakeTaskOutput(
        "Poisson regression models counts; SARIMA models seasonal time series."
    ))
    assert ok is True


def test_build_crew_fetcher_has_tool_access():
    c = crew.build_crew("compare Poisson regression and SARIMA")
    fetcher_agent = c.tasks[0].agent
    assert len(fetcher_agent.tools) == len(crew.crew_tools.FETCH_TOOLS)


def test_build_crew_quant_has_tool_access():
    c = crew.build_crew("compare Poisson regression and SARIMA")
    quant_agent = c.tasks[1].agent
    assert len(quant_agent.tools) == len(crew.crew_tools.QUANT_TOOLS)


def test_run_deep_analysis_catches_exceptions(monkeypatch):
    def boom(raw_input, reasoning_llm=None):
        raise RuntimeError("kickoff exploded")

    monkeypatch.setattr(crew, "build_crew", boom)
    result = crew.run_deep_analysis("anything")
    assert result.startswith("Deep analysis error:")
    assert "kickoff exploded" in result
