"""Manage KEN access: show your own URL, mint/revoke tester links, run the funnel.

    py -3.12 ken_share.py url                  # your own link (owner token)
    py -3.12 ken_share.py invite Sam --hours 8 # tester link, auto-expires
    py -3.12 ken_share.py list                 # active tester links
    py -3.12 ken_share.py revoke <id>          # kill one tester link
    py -3.12 ken_share.py funnel on            # publish to the internet
    py -3.12 ken_share.py funnel off           # stop publishing
    py -3.12 ken_share.py log                  # recent access log

Everything is local: this edits data/.ken_tokens.json and shells out to the
Tailscale CLI. It does not need the server to be running.
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402  (imports the token store + helpers)

TAILSCALE = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
PORT = 8756

# Mount KEN under its own path, never at "/".
#
# This machine already serves other things over Tailscale ("/" -> :8510,
# "/webui/" -> :8080, "/claude/" -> :8501, "/assistant/" -> :8502). Taking "/"
# would hijack one of them, and `tailscale serve reset` would delete ALL of
# them — so on/off here is always scoped to this one path.
MOUNT = "/ken"


def _ts(*args: str) -> str:
    try:
        r = subprocess.run([TAILSCALE, *args], capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return f"tailscale unavailable: {e}"


def _dns_name() -> str | None:
    try:
        data = json.loads(_ts("status", "--json"))
        return (data.get("Self", {}).get("DNSName") or "").rstrip(".") or None
    except Exception:
        return None


def _base_url() -> str:
    host = _dns_name()
    return f"https://{host}{MOUNT}" if host else f"http://127.0.0.1:{PORT}"


def _is_published() -> tuple[bool, bool]:
    """(served_on_tailnet, exposed_to_internet) for KEN's mount path."""
    try:
        st = json.loads(_ts("serve", "status", "--json") or "{}")
    except Exception:
        return (False, False)
    served = any(
        MOUNT.rstrip("/") in (p.rstrip("/") or "/")
        for site in (st.get("Web") or {}).values()
        for p in (site.get("Handlers") or {})
    )
    funnel = bool(st.get("AllowFunnel"))
    return (served, funnel)


def _when(ts: float | None) -> str:
    if not ts:
        return "never"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def cmd_url(_args) -> None:
    if not server.AUTH_ENABLED:
        print("Auth is DISABLED (KEN_TOKEN=\"\"). Anyone who can reach the port gets in.")
        print(f"  {_base_url()}/")
        return
    print("Your personal link (owner access - do NOT share):\n")
    print(f"  {_base_url()}/?token={server.ACCESS_TOKEN}\n")
    print("Open it once per device; a cookie is set and the token leaves the URL.")


def cmd_invite(args) -> None:
    entry = server.create_guest_token(args.name, args.hours)
    print(f"Tester link for {entry['name']} (expires {_when(entry['expires_at'])}):\n")
    print(f"  {_base_url()}/?token={entry['token']}\n")
    print(f"Revoke early:  py -3.12 ken_share.py revoke {entry['id']}")
    if not _dns_name():
        print("\nNOTE: no Tailscale DNS name found - that link only works locally.")
    else:
        print("\nFor someone outside your tailnet, also run:  ken_share.py funnel on")
    print("\nThis grants the SAME access you have, including your vault, memory,")
    print("and the ability to enable run_python on this machine. Only send it to")
    print("someone you trust with that, and revoke it when testing is done.")


def cmd_list(_args) -> None:
    guests = server._active_guests()
    if not guests:
        print("No active tester links.")
        return
    print(f"{'ID':10} {'NAME':16} {'CREATED':17} {'EXPIRES':17}")
    for g in guests:
        print(f"{g['id']:10} {g['name'][:15]:16} {_when(g.get('created_at')):17} {_when(g.get('expires_at')):17}")


def cmd_revoke(args) -> None:
    print(f"Revoked {args.id}." if server.revoke_guest_token(args.id) else f"No such token: {args.id}")


def cmd_funnel(args) -> None:
    if args.state == "on":
        print("Publishing KEN to the public internet via Tailscale Funnel...")
        print(_ts("funnel", "--bg", f"--set-path={MOUNT}", str(PORT)))
        print(f"\nPublic URL: {_base_url()}/")
        print("Anyone can REACH it; only a valid token gets in. Turn it off when done:")
        print("  py -3.12 ken_share.py funnel off   (or the Settings panel in KEN)")
    else:
        # Scoped to KEN's path only. NEVER `serve reset` here: that would
        # delete every other route this machine serves (/, /webui/, /claude/,
        # /assistant/).
        print(_ts("funnel", f"--set-path={MOUNT}", "off"))
        # `funnel ... off` DELETES the route rather than just unpublishing it,
        # which also cut KEN off from the tailnet (phone included). Re-serve.
        print(_ts("serve", "--bg", f"--set-path={MOUNT}", str(PORT)))
        print("Funnel stopped - KEN is off the public internet,")
        print("but still reachable from your own devices on the tailnet.")
        print("(Other Tailscale routes on this machine are untouched.)")


def cmd_serve(args) -> None:
    """Tailnet-only (your own devices), never the public internet."""
    if args.state == "on":
        print(_ts("serve", "--bg", f"--set-path={MOUNT}", str(PORT)))
        print(f"\nTailnet URL: {_base_url()}/   (only your devices)")
    else:
        print(_ts("serve", f"--set-path={MOUNT}", "off"))
        print("KEN unpublished. Other Tailscale routes are untouched.")


def cmd_status(_args) -> None:
    served, funnel = _is_published()
    print(f"KEN mount     : {MOUNT}")
    print(f"On tailnet    : {'yes' if served else 'no'}")
    print(f"Public funnel : {'YES - reachable from the internet' if funnel else 'no'}")
    if served:
        print(f"URL           : {_base_url()}/")


def cmd_log(args) -> None:
    if not server.AUTH_LOG.exists():
        print("No access log yet.")
        return
    lines = server.AUTH_LOG.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.n:]:
        print(line)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("url", help="your own owner link").set_defaults(fn=cmd_url)

    inv = sub.add_parser("invite", help="mint an expiring tester link")
    inv.add_argument("name", nargs="?", default="tester")
    inv.add_argument("--hours", type=int, default=24, help="0 = never expires")
    inv.set_defaults(fn=cmd_invite)

    sub.add_parser("list", help="active tester links").set_defaults(fn=cmd_list)

    rv = sub.add_parser("revoke", help="revoke a tester link")
    rv.add_argument("id")
    rv.set_defaults(fn=cmd_revoke)

    fn = sub.add_parser("funnel", help="publish to / unpublish from the internet")
    fn.add_argument("state", choices=["on", "off"])
    fn.set_defaults(fn=cmd_funnel)

    sv = sub.add_parser("serve", help="tailnet-only access (your devices)")
    sv.add_argument("state", choices=["on", "off"])
    sv.set_defaults(fn=cmd_serve)

    sub.add_parser("status", help="is KEN published? public?").set_defaults(fn=cmd_status)

    lg = sub.add_parser("log", help="recent access log")
    lg.add_argument("-n", type=int, default=30)
    lg.set_defaults(fn=cmd_log)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
