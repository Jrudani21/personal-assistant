#!/usr/bin/env python3
import ctypes as _c, sys as _s
if _s.platform == "win32":
    try: _c.windll.user32.ShowWindow(_c.windll.kernel32.GetConsoleWindow(), 0)
    except Exception: pass
"""GAME MODE daemon — long-running, windowless (anti-lag watchdog).

Launched detached by game_mode_watchdog.py (cron launcher) or manually:
    python qa/game_mode_daemon.py
Polls running processes every POLL_S every 20s via the Windows Toolhelp32
API (ctypes) — NO child processes, NO console windows, zero per-tick cost.
On game launch -> pause heavy cron jobs, stop Ollama, drop background
process priority. On game exit -> resume everything, restart Ollama.

Prints to stdout for manual runs only; the daemon itself is silent
(no_agent cron: transitions are announced by the launcher via state file
watch, and `hermes cron pause/resume` output is suppressed).

Config: data/game_mode.json (same shape as the old qa/game_mode.py).
"""
import ctypes
import json
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "game_mode.json"
STATE = ROOT / "data" / "game_mode_state.json"

NOWIN = 0x08000000  # CREATE_NO_WINDOW — never flash a console
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
POLL_S = 20
HEARTBEAT_STALE_S = 90

DEFAULTS = {
    "processes": ["steam.exe", "epicgameslauncher.exe", "battle.net.exe",
                  "RiotClientServices.exe", "RobloxPlayerBeta.exe",
                  "Valorant.exe", "FortniteClient-Win64-Shipping.exe"],
    "cron_ids": [],
    "pause_ollama": True,
    "low_priority": ["python.exe", "python3.exe", "node.exe", "ollama.exe"],
    "silence_hermes_toasts": True,
}

# ---------------- Windows process listing (ctypes, no subprocess) ----------------
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def list_process_names() -> list[str]:
    """All running exe names (lowercase, no .exe) via Toolhelp32 — no children."""
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    names: list[str] = []
    try:
        if snap == INVALID_HANDLE_VALUE:
            return names
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                try:
                    names.append(pe.szExeFile.lower().removesuffix(".exe"))
                except Exception:
                    pass
                if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return names


# ---------------- state/config ----------------
def load_cfg() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    d = dict(DEFAULTS)
    d.update(cfg or {})
    return d


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"active": False}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def game_running(cfg: dict) -> list[str]:
    want = {n.lower().removesuffix(".exe") for n in cfg["processes"]}
    return [n for n in list_process_names() if n in want]


# ---------------- side effects (all windowless) ----------------
def hermes_cron(action: str, job_id: str) -> bool:
    exe = shutil.which("hermes")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "cron", action, job_id], capture_output=True,
                           text=True, timeout=60, creationflags=NOWIN)
        return r.returncode == 0
    except Exception:
        return False


def hermes_config(key: str, value: str) -> bool:
    """Toggle a Hermes config key (windowless). Used to silence desktop toasts."""
    exe = shutil.which("hermes")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "config", "set", key, value],
                           capture_output=True, text=True, timeout=60,
                           creationflags=NOWIN)
        return r.returncode == 0
    except Exception:
        return False


def ollama_alive() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=2)
        return True
    except Exception:
        return False


def set_priority(names: list, cls: str) -> None:
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
    if cfg.get("silence_hermes_toasts", True):
        hermes_config("display.background_process_notifications", "none")
        hermes_config("display.long_running_notifications", "false")
    return paused


def disable(cfg: dict, paused: list) -> None:
    for jid in paused:
        hermes_cron("resume", jid)
    if cfg["pause_ollama"] and not ollama_alive():
        try:
            subprocess.Popen(["ollama", "serve"], creationflags=NOWIN | DETACHED,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    set_priority(cfg["low_priority"], "Normal")
    if cfg.get("silence_hermes_toasts", True):
        hermes_config("display.background_process_notifications", "all")
        hermes_config("display.long_running_notifications", "true")


# ---------------- main loop ----------------
def main() -> int:
    cfg = load_cfg()
    st = load_state()

    # Singleton: if another daemon is alive (fresh heartbeat), exit quietly.
    if time.time() - float(st.get("hb", 0)) < POLL_S * 2:
        return 0

    # One-shot manual mode for testing (--once: single transition check, exit).
    once = "--once" in sys.argv

    while True:
        games = game_running(cfg)
        st = load_state()
        if games and not st.get("active"):
            paused = enable(cfg)
            save_state({"active": True, "at": datetime.now(timezone.utc).isoformat(),
                        "paused": paused, "games": games, "hb": time.time()})
            print(f"GAME MODE ON — {', '.join(games)}; paused {len(paused)} cron jobs, "
                  f"ollama stopped, priority lowered", flush=True)
        elif not games and st.get("active"):
            disable(cfg, st.get("paused", []))
            save_state({"active": False, "at": datetime.now(timezone.utc).isoformat(),
                        "games": [], "hb": time.time()})
            print(f"GAME MODE OFF — resumed {len(st.get('paused', []))} cron jobs, "
                  f"priority restored", flush=True)
        else:
            # heartbeat refresh only (silent)
            st["hb"] = time.time()
            save_state(st)
        if once:
            break
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
