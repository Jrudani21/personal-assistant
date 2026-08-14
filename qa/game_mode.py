#!/usr/bin/env python3
"""GAME MODE — pause background work when a game is running (anti-lag).

Watchdog (cron, no_agent): on game launch -> pause heavy cron jobs, stop
Ollama (frees RAM/VRAM), drop background process priority. On game exit ->
resume everything. Zero LLM, zero tokens; prints ONLY on state transitions
so a no_agent cron stays silent otherwise.

Modes:
  python qa/game_mode.py            watchdog (default; transition messages only)
  python qa/game_mode.py --check    exit 0 if a configured game is running
  python qa/game_mode.py --status   show state + config
  python qa/game_mode.py --on       force-enable game mode
  python qa/game_mode.py --off      force-disable game mode

Config: data/game_mode.json
  {"processes": ["game.exe", ...],        # match case-insensitive, .exe optional
   "cron_ids": ["<hermes cron job id>"],  # jobs paused while gaming
   "pause_ollama": true,                  # stop Ollama server on game launch
   "low_priority": ["python.exe", ...]}   # procs dropped to BelowNormal
"""
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "game_mode.json"
STATE = ROOT / "data" / "game_mode_state.json"

# Windows: spawn children WITHOUT a visible console (no cmd flash on screen).
NOWIN = 0x08000000  # CREATE_NO_WINDOW

DEFAULTS = {
    "processes": ["steam.exe", "epicgameslauncher.exe", "battle.net.exe",
                  "RiotClientServices.exe", "RobloxPlayerBeta.exe",
                  "Valorant.exe", "FortniteClient-Win64-Shipping.exe"],
    "cron_ids": [],
    "pause_ollama": True,
    "low_priority": ["python.exe", "python3.exe", "node.exe", "ollama.exe"],
}


def load_cfg() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    d = dict(DEFAULTS)
    d.update(cfg or {})
    return d


def save_state(state: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"active": False}


def running_procs(names: list) -> list[str]:
    """Return matching process names (case-insensitive, .exe optional)."""
    want = {n.lower().removesuffix(".exe") for n in names}
    out = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
        timeout=30, creationflags=NOWIN).stdout
    found = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) < 1:
            continue
        pname = parts[0].strip().strip('"').lower().removesuffix(".exe")
        if pname in want:
            found.append(parts[0].strip().strip('"'))
    return found


def game_running(cfg: dict) -> list[str]:
    return running_procs(cfg["processes"])


def hermes_cron(action: str, job_id: str) -> bool:
    exe = shutil.which("hermes")
    if not exe:
        print("  !! hermes CLI not found — cannot pause/resume cron")
        return False
    r = subprocess.run([exe, "cron", action, job_id], capture_output=True,
                       text=True, timeout=60, creationflags=NOWIN)
    return r.returncode == 0


def ollama_alive() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=2)
        return True
    except Exception:
        return False


def set_priority(names: list, cls: str) -> None:
    """Drop/restore priority of background processes (BelowNormal/Normal)."""
    if not names:
        return
    pats = ",".join(n.removesuffix(".exe") + "*" for n in names)
    ps = ("powershell -NoProfile -Command "
          f"\"Get-Process {pats} -ErrorAction SilentlyContinue | "
          f"ForEach-Object {{ $_.PriorityClass = '{cls}' }}\"")
    try:
        subprocess.run(ps, shell=True, capture_output=True, timeout=60,
                       creationflags=NOWIN)
    except Exception:
        pass


def enable(cfg: dict) -> list[str]:
    paused = []
    for jid in cfg["cron_ids"]:
        if hermes_cron("pause", jid):
            paused.append(jid)
    if cfg["pause_ollama"] and ollama_alive():
        try:
            subprocess.run(["ollama", "stop"], capture_output=True, timeout=60,
                           creationflags=NOWIN)
        except Exception:
            pass
    set_priority(cfg["low_priority"], "BelowNormal")
    return paused


def disable(cfg: dict, paused: list) -> None:
    for jid in paused:
        hermes_cron("resume", jid)
    if cfg["pause_ollama"] and not ollama_alive():
        # Restart Ollama server detached (tray app may not re-serve on demand).
        try:
            subprocess.Popen(["ollama", "serve"], creationflags=0x08000000)  # CREATE_NO_WINDOW
        except Exception:
            pass
    set_priority(cfg["low_priority"], "Normal")


def main() -> int:
    cfg = load_cfg()
    st = load_state()

    if "--check" in sys.argv:
        return 0 if game_running(cfg) else 1
    if "--status" in sys.argv:
        games = game_running(cfg)
        print(f"game running: {games or 'no'}")
        print(f"state: {'ON' if st.get('active') else 'off'} "
              f"(at {st.get('at', '-')})")
        print(f"config: processes={cfg['processes']} cron_ids={cfg['cron_ids']}")
        return 0
    if "--on" in sys.argv:
        if st.get("active"):
            print("GAME MODE already ON"); return 0
        paused = enable(cfg)
        save_state({"active": True, "at": datetime.now(timezone.utc).isoformat(),
                    "paused": paused})
        print(f"GAME MODE ON (manual) — paused {len(paused)} cron jobs, "
              f"ollama {'stopped' if cfg['pause_ollama'] else 'untouched'}, "
              f"priority lowered")
        return 0
    if "--off" in sys.argv:
        if not st.get("active"):
            print("GAME MODE already off"); return 0
        disable(cfg, st.get("paused", []))
        save_state({"active": False, "at": datetime.now(timezone.utc).isoformat()})
        print(f"GAME MODE OFF (manual) — resumed {len(st.get('paused', []))} cron jobs")
        return 0

    # ---- watchdog (cron) ----
    games = game_running(cfg)
    if games and not st.get("active"):
        paused = enable(cfg)
        save_state({"active": True, "at": datetime.now(timezone.utc).isoformat(),
                    "paused": paused})
        print(f"GAME MODE ON — {', '.join(games)} running; paused "
              f"{len(paused)} cron jobs, ollama stopped, priority lowered")
        return 0
    if not games and st.get("active"):
        disable(cfg, st.get("paused", []))
        save_state({"active": False, "at": datetime.now(timezone.utc).isoformat()})
        print(f"GAME MODE OFF — resumed {len(st.get('paused', []))} cron jobs, "
              f"priority restored")
        return 0
    return 0  # silent: no transition


if __name__ == "__main__":
    raise SystemExit(main())
