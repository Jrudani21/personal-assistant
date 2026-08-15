"""🎩 admin — a tool that lets the Personal Assistant administer the AI fleet.

This connects the Personal Assistant to the Admin Bot's capabilities so you
can just *ask* your assistant (in plain language) to check status, run a crew
sweep, read logs, restart a Docker container, etc.

The actual long-running auto-heal + alerts happen in admin_daemon.py
(projects/admin-bot/). This module is the "ask me anything, I'll admin it"
front door used by the assistant's tool-calling loop.
"""
import os
import subprocess
import json

HOME = r"C:\Users\Janak's PC"
PROJECTS = r"E:\Local\projects"  # deepseek-cave/admin-bot live here since the 2026-08-11 drive migration
CAVE = os.path.join(PROJECTS, "deepseek-cave")
CREW = os.path.join(CAVE, "crew")
CREW_LOG = os.path.join(CREW, "crew-log.md")
LEARN_LOG = os.path.join(CAVE, "learn-log.md")
SYS_MON_LOG = os.path.join(CAVE, "system-monitor.log")
PY = r"C:\Users\Janak's PC\AppData\Local\Programs\Python\Python312\python.exe"  # Python install itself, not moved
OLLAMA = "http://localhost:11434"


def _port_open(port, host="127.0.0.1", timeout=1.5):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def admin_status():
    """Live status of the whole fleet as a readable string."""
    import requests
    lines = []
    # Ollama
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        lines.append(f"Ollama: UP ({len(models)} models: {', '.join(models)})")
    except Exception:
        lines.append("Ollama: DOWN")
    # Apps
    apps = {
        "KEN / Personal Assistant (8756)": 8756,
        "Open WebUI (8080)": 8080,
        "Health server (9999)": 9999,
    }
    for name, port in apps.items():
        lines.append(f"{name}: {'UP' if _port_open(port) else 'DOWN'}")
    # Docker
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"],
                             capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        lines.append("Docker:\n" + (out.strip() or "  (none running)"))
    except Exception as e:
        lines.append(f"Docker: error ({e})")
    # Crew heartbeat
    try:
        import time as _t
        age = _t.time() - os.path.getmtime(CREW_LOG)
        lines.append(f"System Crew: last sweep {age/60:.0f} min ago ({'healthy' if age < 3600 else 'STALE!'})")
    except Exception as e:
        lines.append(f"System Crew: unknown ({e})")
    return "\n".join(lines)


def admin_crew_sweep():
    """Run a full system-crew sweep now; return the output tail."""
    try:
        proc = subprocess.run([PY, os.path.join(CREW, "crew.py")], capture_output=True,
                              text=True, timeout=180, cwd=CAVE, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (proc.stdout or "")[-2500:]
    except subprocess.TimeoutExpired:
        return "Crew sweep timed out (>180s)"
    except Exception as e:
        return f"Crew sweep failed: {e}"


def admin_crew_member(name):
    try:
        proc = subprocess.run([PY, os.path.join(CREW, "crew.py"), name], capture_output=True,
                              text=True, timeout=120, cwd=CAVE, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (proc.stdout or "")[-1500:]
    except Exception as e:
        return f"Member {name} failed: {e}"


def admin_log(source="crew-log", lines=40):
    paths = {"crew-log": CREW_LOG, "learn-log": LEARN_LOG, "system-monitor": SYS_MON_LOG}
    path = paths.get(source, CREW_LOG)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-int(lines):])
    except Exception as e:
        return f"Can't read {source}: {e}"


def admin_docker(action="list", name=None):
    """action: list | start | stop | restart"""
    try:
        if action == "list":
            out = subprocess.run(["docker", "ps", "--format", "{{.Names}} | {{.Status}} | {{.Ports}}"],
                                 capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return out.stdout.strip() or "(no containers running)"
        if not name:
            return "No container name given."
        out = subprocess.run(["docker", action, name], capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or out.stderr.strip() or f"{action} {name}: ok"
    except Exception as e:
        return f"docker {action}: {e}"


def admin_open_webui():
    exe = os.path.join(HOME, "projects", "open-webui-venv", "Scripts", "open-webui.exe")
    if _port_open(8080):
        return "Open WebUI already running on :8080"
    try:
        subprocess.Popen([exe, "serve", "--host", "127.0.0.1", "--port", "8080"], cwd=HOME,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
        return "Starting Open WebUI on :8080 (give it ~15s)..."
    except Exception as e:
        return f"Failed to start Open WebUI: {e}"


# ---------- tool registration for the assistant ----------
def admin(params: dict) -> str:
    """Admin the AI fleet: status, crew sweeps, logs, docker, start Open WebUI."""
    action = (params or {}).get("action", "status")
    name = (params or {}).get("name")
    source = (params or {}).get("source", "crew-log")
    lines = (params or {}).get("lines", 40)

    if action == "status":
        return admin_status()
    if action == "crew_sweep":
        return admin_crew_sweep()
    if action == "crew_member":
        return admin_crew_member(name or "doctor")
    if action == "log":
        return admin_log(source, lines)
    if action == "docker":
        return admin_docker(name=name)
    if action == "docker_start":
        return admin_docker("start", name)
    if action == "docker_stop":
        return admin_docker("stop", name)
    if action == "docker_restart":
        return admin_docker("restart", name)
    if action == "open_webui":
        return admin_open_webui()
    return "Unknown admin action. Use: status, crew_sweep, crew_member, log, docker, docker_start, docker_stop, docker_restart, open_webui"


def get_registry_extra():
    """Return a dict for tools.py REGISTRY merge."""
    return {"admin": admin}


def get_schemas_extra():
    """Return a schema for the assistant's tool list."""
    return {
        "type": "function",
        "function": {
            "name": "admin",
            "description": (
                "Admin the AI fleet. Actions: status (live status of Ollama, apps, Docker, crew), "
                "crew_sweep (run a full system-crew sweep), crew_member (run one crew member: "
                "doctor/engineer/ops/datakeeper/security/policeman/websage/backup), log (read a log: "
                "crew-log/learn-log/system-monitor), docker (list containers), docker_start/docker_stop/"
                "docker_restart (control a container by name), open_webui (start Open WebUI on 8080)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Which admin action to run"},
                    "name": {"type": "string", "description": "Container name or crew member name"},
                    "source": {"type": "string", "description": "Log source"},
                    "lines": {"type": "integer", "description": "Log lines to read"},
                },
                "required": ["action"],
            },
        },
    }
