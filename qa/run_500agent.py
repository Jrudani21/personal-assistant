#!/usr/bin/env python
"""Generic runner for 500-AI-Agents-Projects agents (DeepSeek-swapped).

Invokes a script agent in the sibling repo with the correct interpreter and
environment (env -u PYTHONPATH so the Hermes venv can't shadow the project venv),
captures output to data/agents/<id>.log, and exits with the agent's status.

Usage:
  python qa/run_500agent.py 09-resume-parser-agent --resume <file>
  python qa/run_500agent.py 18-job-application-agent --job-desc "..." --candidate "..."
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_REPO = Path(os.environ.get("AGENTS_500_REPO", "E:/Local/projects/500-AI-Agents-Projects"))
VENV_PY = AGENTS_REPO / ".venv" / "Scripts" / "python.exe"
LOG_DIR = ROOT / "data" / "agents"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_500agent.py <agent-dir> [agent args...]", file=sys.stderr)
        return 2

    agent_dir = sys.argv[1]
    agent_script = AGENTS_REPO / "agents" / agent_dir / "agent.py"
    if not agent_script.exists():
        print(f"AGENT_NOT_FOUND {agent_dir} ({agent_script})", file=sys.stderr)
        return 1

    if not VENV_PY.exists():
        print(f"VENV_NOT_FOUND {VENV_PY}", file=sys.stderr)
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{agent_dir}.log"

    # Strip PYTHONPATH so the Hermes desktop venv can't shadow the project venv
    # (known issue: tokenizers version collision). DeepSeek key is a Windows env var.
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    cmd = [str(VENV_PY), str(agent_script), *sys.argv[2:]]
    print(f"RUN {agent_dir}: {' '.join(cmd)}", flush=True)

    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== run {agent_dir} {' '.join(sys.argv[2:])} ===\n")
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=str(AGENTS_REPO / "agents" / agent_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log.write(proc.stdout)
        log.write(f"--- exit {proc.returncode} ---\n")

    print(proc.stdout)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
