"""Game-mode cron launcher — starts the windowless daemon if it's not alive.

The daemon (qa/game_mode_daemon.py) polls game processes via the Windows
Toolhelp32 API and pauses/resumes cron jobs + Ollama, all with NO console
windows. This launcher just ensures the daemon is running, then exits fast
(<1s) — so even if the cron scheduler opens a console per tick, the pop is
brief and only when the daemon actually needs starting.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"E:\Local\projects\personal-assistant")
STATE = ROOT / "data" / "game_mode_state.json"
DAEMON = ROOT / "qa" / "game_mode_daemon.py"

NOWIN = 0x08000000
DETACHED = 0x00000008 | 0x00000200


def daemon_alive() -> bool:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        return time.time() - float(st.get("hb", 0)) < 90
    except Exception:
        return False


def main() -> int:
    if daemon_alive():
        return 0  # silent — daemon handles everything
    # Spawn the daemon detached + windowless; it survives our exit.
    subprocess.Popen(
        [sys.executable, str(DAEMON)],
        creationflags=NOWIN | DETACHED,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    # Give it a beat to write its first heartbeat, then exit.
    time.sleep(1.5)
    print("game-mode daemon started") if not daemon_alive() else None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
