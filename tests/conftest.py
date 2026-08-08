import pytest

from assistant.tools import WORKSPACE


@pytest.fixture
def tmp_workspace_file():
    """A real small CSV under data/workspace/, cleaned up after the test."""
    content = "product,units_sold,revenue\nA,120,3600\nB,80,4000\nC,200,5000\n"
    path = WORKSPACE / "pytest_sales_test.csv"
    path.write_text(content, encoding="utf-8")
    try:
        yield path, content
    finally:
        path.unlink(missing_ok=True)


class FakeTaskOutput:
    """Minimal stand-in for crewai.tasks.task_output.TaskOutput, which
    guardrail callables only ever read `.raw` off of."""

    def __init__(self, raw):
        self.raw = raw
