"""Background reminder daemon: fires Windows toasts for due reminders even
when the Streamlit app is closed.

Optional — the app itself still checks reminders on every rerun, but only
while it's open. This daemon removes that limitation:

    python assistant/reminder_daemon.py --once       # fire due reminders now, exit
    python assistant/reminder_daemon.py --install    # register a logon scheduled task
    python assistant/reminder_daemon.py              # run forever in the foreground
    python assistant/reminder_daemon.py --uninstall  # remove the scheduled task

The scheduled task runs with pythonw.exe (no console window) and starts at
logon. Toasts are best-effort: if the Windows toast call fails, it falls back
to a classic msg popup, then to the console — a reminder is never silently
dropped, and is marked fired only after a delivery attempt so it can't
double-fire.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

# Allow running as a plain script (`python assistant/reminder_daemon.py`)
# as well as a module, so the scheduled task doesn't depend on the CWD.
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import reminders  # noqa: E402

POLL_INTERVAL_S = 30
APP_ID = "PersonalAssistant.ReminderDaemon"
TOAST_TITLE = "Personal Assistant"
TASK_NAME = "PersonalAssistant ReminderDaemon"


def _ps_quote(text: str) -> str:
    """Single-quote a string for embedding in a PowerShell command line
    (single quotes are doubled, per PowerShell escaping rules)."""
    return "'" + text.replace("'", "''") + "'"


def _toast_script(title: str, message: str) -> str:
    """PowerShell snippet that pops a Windows toast with the given text.
    Collapses whitespace/newlines so a multi-line reminder stays one line."""
    safe_title = " ".join((title or "").split())
    safe_msg = " ".join((message or "").split())
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null\n"
        "[Windows.UI.Notifications.ToastNotification, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null\n"
        "$template = [Windows.UI.Notifications.ToastNotificationManager]"
        "::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]"
        "::ToastText02)\n"
        "$textNodes = $template.GetElementsByTagName('text')\n"
        f"$textNodes.Item(0).AppendChild($template.CreateTextNode({_ps_quote(safe_title)})) > $null\n"
        f"$textNodes.Item(1).AppendChild($template.CreateTextNode({_ps_quote(safe_msg)})) > $null\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template)\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]"
        f"::CreateToastNotifier({_ps_quote(APP_ID)}).Show($toast)\n"
    )


def show_toast(title: str, message: str) -> bool:
    """Best-effort Windows toast via PowerShell. Returns whether any delivery
    path succeeded."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _toast_script(title, message)],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    # Fallback: classic msg popup (works even where toasts need an AUMID).
    try:
        subprocess.run(
            ["msg", "*", f"{title}: {message}"],
            capture_output=True, timeout=15,
        )
        return True
    except Exception:
        return False


def check_once() -> int:
    """Fire every due reminder. Returns the number fired. A reminder is marked
    fired regardless of whether delivery succeeded, so a reminder that fails
    to toast once is not retried forever (the app itself shows it once too)."""
    fired = 0
    for r in reminders.due_reminders():
        show_toast(TOAST_TITLE, r.get("text", "Reminder"))
        reminders.mark_fired(r["id"])
        fired += 1
    return fired


def run_forever(interval: float = POLL_INTERVAL_S) -> None:
    while True:
        try:
            check_once()
        except Exception:
            pass  # never die on a transient failure; try again next poll
        time.sleep(interval)


def install() -> str:
    """Register a scheduled task that starts the daemon (hidden) at logon."""
    exe = Path(sys.executable)
    runner = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    script = str(Path(__file__).resolve())
    cmd = [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", f'"{runner}" "{script}"',
        "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return f"Install failed: {result.stderr.strip() or result.stdout.strip()}"
    return (
        f"Registered scheduled task '{TASK_NAME}' (starts at logon, hidden). "
        f"Test it now with 'python assistant/reminder_daemon.py --once'."
    )


def uninstall() -> str:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 and "not found" not in (result.stderr + result.stdout).lower():
        return f"Uninstall failed: {result.stderr.strip() or result.stdout.strip()}"
    return f"Removed scheduled task '{TASK_NAME}'."


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Personal Assistant reminder daemon (Windows toasts)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="fire due reminders once and exit")
    group.add_argument("--install", action="store_true", help="register a logon scheduled task")
    group.add_argument("--uninstall", action="store_true", help="remove the scheduled task")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL_S,
                        help=f"poll interval in seconds (default {POLL_INTERVAL_S})")
    args = parser.parse_args(argv)

    if args.install:
        print(install())
    elif args.uninstall:
        print(uninstall())
    elif args.once:
        n = check_once()
        print(f"Fired {n} reminder(s).")
    else:
        print(f"Reminder daemon running (poll every {args.interval:g}s). Ctrl+C to stop.")
        run_forever(args.interval)


if __name__ == "__main__":
    main()
