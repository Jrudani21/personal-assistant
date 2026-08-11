"""KEN — local web frontend for the personal assistant (one-command launcher).

Replaces the old Streamlit app.py. This starts the FastAPI backend from
`server.py` (which wraps the `assistant` package — nothing there is
modified), serves the KEN web UI (`ken.html`), and opens your browser.

Run:
    python app.py          # or:  py -3.12 app.py
"""

from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

from server import ACCESS_TOKEN, AUTH_ENABLED, BASE_DIR, MODEL, app  # noqa: F401

# Bind to loopback ONLY. Phone/remote access goes through `tailscale serve`
# (tailnet) or `tailscale funnel` (public), which proxy to 127.0.0.1 over
# WireGuard with a real TLS cert. Binding 0.0.0.0 instead would also expose
# KEN on the Ethernet interface, whose Windows firewall profile is "Public".
HOST = "127.0.0.1"
PORT = 8756
URL = f"http://{HOST}:{PORT}"


def _open_browser() -> None:
    """Wait for the server to come up, then open the browser once."""
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(URL + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    # ?token= is swapped for an HttpOnly cookie and redirected away on arrival.
    webbrowser.open(f"{URL}/?token={ACCESS_TOKEN}" if AUTH_ENABLED else URL)


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"KEN running at {URL}  (model: {MODEL})")
    if AUTH_ENABLED:
        print(f"  owner link : {URL}/?token={ACCESS_TOKEN}")
        print("  phone/share: py -3.12 ken_share.py url | invite <name> | funnel on")
    else:
        print("  WARNING: auth disabled (KEN_TOKEN=\"\") — anyone who can reach "
              "this port has full access, including run_python.")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)
