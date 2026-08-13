"""
Crew sweep script — runs the 4-agent deep analysis pipeline on a topic.
Called by the JanakCrewSweep cron job. Accepts a topic as the first argument
or via DEEP_ANALYSIS_TOPIC env var, defaults to a system health check.
"""
import sys
import os

sys.path.insert(0, r"E:\Local\projects\personal-assistant")

from assistant.crew import run_deep_analysis

topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else os.environ.get(
    "DEEP_ANALYSIS_TOPIC",
    "system health check: verify Ollama models, Graphiti graph, and disk space"
)

result = run_deep_analysis(topic)
print(result)
