"""Skills — reusable procedures the assistant can run.

Anything-llm / private-gpt research pattern: instead of hardcoding every
procedure as a tool, a *skill* is a folder under data/skills/ that the model
can discover and execute:

    data/skills/<name>/SKILL.md   — instructions (returned to the model)
    data/skills/<name>/run.py     — optional executable: run(args: str) -> str

`list_skills` returns what's available; `run_skill` either returns the
SKILL.md instructions (so the model carries them out with its existing tools)
or, when run.py is present, executes it and returns its output. Skills are
sandboxed to their own folder and run with the current interpreter.
"""
import subprocess
import sys
from pathlib import Path

from . import config as _config

SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _skills_root() -> Path:
    cfg = _config.get("skills_dir")
    p = Path(cfg).resolve() if cfg else SKILLS_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_skills() -> str:
    """List available skills with a one-line summary from each SKILL.md."""
    root = _skills_root()
    skills = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not skills:
        return (
            "No skills yet. Create a folder under "
            f"{root} with a SKILL.md (instructions) and optionally run.py."
        )
    lines = []
    for d in skills:
        md = d / "SKILL.md"
        summary = ""
        if md.exists():
            for ln in md.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    summary = ln[:120]
                    break
        runnable = " (script)" if (d / "run.py").exists() else ""
        lines.append(f"- {d.name}{runnable}" + (f" — {summary}" if summary else ""))
    return "\n".join(lines)


def run_skill(name: str, args: str = "") -> str:
    """Run a skill: execute run.py if present, otherwise return SKILL.md so
    the model can follow the instructions with its normal tools."""
    root = _skills_root()
    # name is user/model supplied — resolve inside the skills root only
    try:
        d = (root / name).resolve()
        if root not in d.parents and d != root:
            raise ValueError("Skill name escapes the skills folder.")
    except Exception as e:
        return f"Error: {e}"
    if not d.is_dir():
        return f"No skill named '{name}'. Available: {list_skills() or '(none)'}"

    script = d / "run.py"
    if script.exists():
        try:
            timeout = float(_config.get("skill_timeout_s", 60))
            r = subprocess.run(
                [sys.executable, "-u", str(script), args],
                capture_output=True, text=True, timeout=timeout, cwd=d,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            parts = []
            if r.stdout.strip():
                parts.append(r.stdout.strip())
            if r.stderr.strip():
                parts.append("[stderr]\n" + r.stderr.strip())
            if r.returncode != 0:
                parts.append(f"(exit code {r.returncode})")
            return "\n".join(parts).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Skill '{name}' timed out."
        except Exception as e:
            return f"Skill '{name}' error: {e}"

    md = d / "SKILL.md"
    if md.exists():
        return f"Instructions for '{name}':\n\n{md.read_text(encoding='utf-8').strip()}"
    return f"Skill '{name}' has no SKILL.md or run.py."
