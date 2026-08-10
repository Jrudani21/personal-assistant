"""Local snapshot backups of the assistant's data.

Everything the assistant knows lives in data/ (memory, chats, observations,
embeddings, tasks, reminders, workspace files). Git tracks only memory.json;
the rest has no version history. This module snapshots the whole data/
directory into timestamped backup folders, so the assistant keeps a local
history independent of GitHub:

    backups/
      2026-08-09_213000/
      2026-08-08_093015/
      ...

Design:
- Each backup is a plain folder copy of data/ — transparent, inspectable,
  restorable by hand if ever needed.
- Retention: keep the newest MAX_BACKUPS (default 10); older snapshots are
  pruned. A run never deletes the newest backup.
- Writes go to a temp dir then rename, so a crash mid-backup can't leave a
  half-written snapshot.
- Auto-backup convenience: `backup_if_due()` creates one backup per day,
  for wiring into app startup or the daemon loop.
"""
import datetime
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BACKUP_ROOT = Path(__file__).resolve().parent.parent / "backups"
MAX_BACKUPS = 10

_TS_FORMAT = "%Y-%m-%d_%H%M%S_%f"
_DAY_FORMAT = "%Y-%m-%d"


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def create_backup(keep: int | None = None) -> str:
    """Snapshot data/ into a timestamped folder. Returns a summary message."""
    if keep is None:
        keep = MAX_BACKUPS
    if not DATA_DIR.exists():
        return "No data directory to back up."

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    ts = _now().strftime(_TS_FORMAT)
    dest = BACKUP_ROOT / ts

    # copy to a temp dir first, then atomically rename into place
    tmp = BACKUP_ROOT / f".tmp_{ts}"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(DATA_DIR, tmp)
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)

    pruned = _prune(keep)
    msg = f"Backed up data to backups/{ts}"
    if pruned:
        msg += f" (pruned {pruned} old backup{'s' if pruned != 1 else ''})"
    return msg + "."


def _prune(keep: int) -> int:
    """Delete oldest backups beyond `keep`. Never deletes the newest."""
    dirs = sorted(
        (d for d in BACKUP_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".tmp")),
        key=lambda d: d.name,
    )
    if len(dirs) <= keep:
        return 0
    removed = 0
    for d in dirs[:-keep]:
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return removed


def list_backups() -> list[str]:
    """Backup folder names, newest first."""
    if not BACKUP_ROOT.exists():
        return []
    dirs = [d.name for d in BACKUP_ROOT.iterdir()
            if d.is_dir() and not d.name.startswith(".tmp")]
    return sorted(dirs, reverse=True)


def latest_backup_time() -> str | None:
    """ISO timestamp of the newest backup, or None."""
    backups = list_backups()
    if not backups:
        return None
    try:
        return datetime.datetime.strptime(backups[0], _TS_FORMAT).isoformat()
    except ValueError:
        return None


def backup_if_due() -> str | None:
    """Create a backup if none exists for today. Returns the message, or
    None when today's backup already exists (no-op)."""
    today = _now().strftime(_DAY_FORMAT)
    if any(b.startswith(today) for b in list_backups()):
        return None
    return create_backup()


def restore_backup(name: str) -> str:
    """Replace data/ with the contents of a backup. Destructive to current
    data — callers should confirm first."""
    src = BACKUP_ROOT / name
    if not src.is_dir():
        return f"No backup named '{name}'. Available: {', '.join(list_backups()) or '(none)'}"

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    shutil.copytree(src, DATA_DIR)
    return f"Restored data from backup '{name}'."


def size_bytes() -> int:
    """Total size of all backups, for the UI."""
    total = 0
    for d in BACKUP_ROOT.glob("*"):
        if d.is_dir() and not d.name.startswith(".tmp"):
            for f in d.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
    return total
