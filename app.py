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

from server import BASE_DIR, MODEL, app  # noqa: F401  (re-exports the API app)

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
    webbrowser.open(URL)


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"KEN running at {URL}  (model: {MODEL})")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)
