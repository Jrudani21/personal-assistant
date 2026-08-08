"""
Standalone CrewAI smoke test for the hybrid pipeline in assistant/crew.py.
Same pipeline used by the `deep_analysis` chat tool and the sidebar's
"Deep Analysis" section in app.py -- run this directly to sanity-check the
setup (Ollama reachable, Claude Code CLI reachable) without going through
the UI.

Setup:
    pip install crewai
    ollama serve                     # must already be running
    ollama pull qwen2.5:7b            # or whichever model you have pulled
    claude --version                 # Claude Code CLI must be installed
                                      # and already logged in (claude login)

Run:
    python crewai_demo.py
"""
import sys

from assistant.crew import run_deep_analysis

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

TOPIC = "time-series forecasting methods for count data (e.g. Poisson regression vs SARIMA)"

if __name__ == "__main__":
    result = run_deep_analysis(TOPIC)
    print("\n=== FINAL REPORT ===\n")
    print(result)
