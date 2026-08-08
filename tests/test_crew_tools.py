from assistant import crew_tools
from assistant import rag
from assistant import tools as assistant_tools


# ---------- thin wrappers delegate correctly ----------

def test_web_search_tool_delegates_to_assistant_tools(monkeypatch):
    monkeypatch.setattr(assistant_tools, "web_search", lambda query, max_results=5: f"searched:{query}:{max_results}")
    assert crew_tools.web_search_tool.run(query="ollama", max_results=3) == "searched:ollama:3"


def test_wikipedia_summary_tool_delegates(monkeypatch):
    monkeypatch.setattr(assistant_tools, "wikipedia_summary", lambda topic: f"wiki:{topic}")
    assert crew_tools.wikipedia_summary_tool.run(topic="Poisson distribution") == "wiki:Poisson distribution"


def test_read_file_tool_delegates(monkeypatch):
    monkeypatch.setattr(assistant_tools, "read_file", lambda path: f"content-of:{path}")
    assert crew_tools.read_file_tool.run(path="a.txt") == "content-of:a.txt"


def test_list_files_tool_delegates(monkeypatch):
    monkeypatch.setattr(assistant_tools, "list_files", lambda subpath="": f"listing:{subpath}")
    assert crew_tools.list_files_tool.run(subpath="sub") == "listing:sub"


def test_search_documents_tool_delegates_to_rag_module(monkeypatch):
    monkeypatch.setattr(rag, "search_documents", lambda query: f"docs:{query}")
    assert crew_tools.search_documents_tool.run(query="RAG") == "docs:RAG"


def test_calculator_tool_delegates(monkeypatch):
    monkeypatch.setattr(assistant_tools, "calculator", lambda expression: f"calc:{expression}")
    assert crew_tools.calculator_tool.run(expression="1+1") == "calc:1+1"


# ---------- run_python_tool: real isolated subprocess ----------

def test_run_python_tool_executes_and_captures_stdout():
    out = crew_tools.run_python_tool.run(code="print(2 + 2)")
    assert out.strip() == "4"


def test_run_python_tool_captures_stderr_alongside_stdout():
    out = crew_tools.run_python_tool.run(
        code="import sys; sys.stderr.write('a warning'); print('done')"
    )
    assert "done" in out
    assert "a warning" in out


def test_run_python_tool_reports_no_output():
    out = crew_tools.run_python_tool.run(code="x = 1")
    assert out == "(no output)"


def test_run_python_tool_reports_exceptions_without_crashing():
    out = crew_tools.run_python_tool.run(code="1 / 0")
    assert "error" in out.lower() or "zerodivisionerror" in out.lower()


def test_run_python_tool_times_out_gracefully(monkeypatch):
    import subprocess

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="python", timeout=15)

    monkeypatch.setattr(crew_tools.subprocess, "run", fake_run)
    out = crew_tools.run_python_tool.run(code="import time; time.sleep(999)")
    assert "timed out" in out.lower()


def test_run_python_tool_is_isolated_from_the_shared_repl_session():
    # would collide with a variable the persistent chat REPL might hold,
    # if this tool reused that shared session instead of a fresh process
    crew_tools.run_python_tool.run(code="x = 999")
    out = crew_tools.run_python_tool.run(code="print('x' in dir())")
    assert out.strip() == "False"


def test_run_python_tool_runs_with_workspace_as_cwd():
    out = crew_tools.run_python_tool.run(code="import os; print(os.getcwd())")
    assert out.strip().replace("\\", "/").endswith("data/workspace")


# ---------- tool groupings ----------

def test_fetch_tools_do_not_include_execution_tools():
    names = {t.name for t in crew_tools.FETCH_TOOLS}
    assert "run_python" not in names
    assert "calculator" not in names


def test_quant_tools_only_contains_calculation_tools():
    names = {t.name for t in crew_tools.QUANT_TOOLS}
    assert names == {"calculator", "run_python"}
