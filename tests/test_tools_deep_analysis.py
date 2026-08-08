from assistant import crew, tools


def test_deep_analysis_registered_in_registry_and_schemas():
    assert tools.REGISTRY["deep_analysis"] is tools.deep_analysis
    names = {s["function"]["name"] for s in tools.SCHEMAS}
    assert "deep_analysis" in names


def test_deep_analysis_delegates_to_crew_run_deep_analysis(monkeypatch):
    monkeypatch.setattr(crew, "run_deep_analysis", lambda raw_input: f"report about {raw_input}")
    assert tools.deep_analysis("count regression") == "report about count regression"


def test_deep_analysis_catches_import_or_runtime_errors(monkeypatch):
    def boom(raw_input):
        raise RuntimeError("crewai not installed")

    monkeypatch.setattr(crew, "run_deep_analysis", boom)
    result = tools.deep_analysis("anything")
    assert result.startswith("Error:")
    assert "crewai not installed" in result
