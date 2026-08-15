"""CDP-driven responsive layout audit for KEN (login + app) at phone widths. v3
Robust: picks the page target, waits for Page.loadEventFired, reports page state."""
import base64
import json
import os
import socket
import subprocess
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = os.environ.get("LOCALAPPDATA", "") + r"\Temp\ken-cdp-profile"
os.makedirs(BASE, exist_ok=True)
SHOTS = os.environ.get("LOCALAPPDATA", "") + r"\Temp\kenshots"
os.makedirs(SHOTS, exist_ok=True)

AUDIT_JS = """(() => {
  const doc = document.documentElement;
  const card = document.querySelector('.card');
  const r = card ? card.getBoundingClientRect() : null;
  const sb = document.querySelector('.sidebar');
  const sr = sb ? sb.getBoundingClientRect() : null;
  const tb = document.querySelector('.topbar');
  const tr = tb ? tb.getBoundingClientRect() : null;
  return {
    title: document.title,
    url: location.href,
    vw: window.innerWidth,
    vh: window.innerHeight,
    scrollW: doc.scrollWidth,
    hOverflow: doc.scrollWidth > window.innerWidth,
    card: r ? {left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width)} : null,
    sidebar: sr ? {left: Math.round(sr.left), width: Math.round(sr.width)} : null,
    topbar: tr ? {left: Math.round(tr.left), right: Math.round(tr.right)} : null,
  };
})()"""

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def audit(port, url, width, height, label, token=None):
    proc = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={port}", "--headless=new", "--disable-gpu",
         "--remote-allow-origins=*",
         f"--user-data-dir={BASE}\\{label}", "--no-first-run", "--no-default-browser-check",
         "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ws = None
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1); break
            except Exception:
                time.sleep(0.25)
        time.sleep(1.0)
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
        page = next(t for t in tabs if t.get("type") == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=25)
        mid = {"n": 0}
        def cmd(method, params=None, timeout=25):
            mid["n"] += 1
            i = mid["n"]
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            deadline = time.time() + timeout
            while time.time() < deadline:
                ws.settimeout(deadline - time.time())
                try:
                    m = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    break
                if m.get("id") == i:
                    return m.get("result", {})
            raise TimeoutError(f"{method} timed out")
        cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 2, "mobile": True})
        cmd("Page.enable")
        nav_url = url + (f"?token={token}" if token else "")
        cmd("Page.navigate", {"url": nav_url})
        time.sleep(7.0)
        res = cmd("Runtime.evaluate", {"expression": AUDIT_JS, "returnByValue": True})
        val = res.get("result", {}).get("value", {})
        print(f"{label} ({width}x{height}): title={val.get('title')!r} vw={val.get('vw')} "
              f"scrollW={val.get('scrollW')} overflow={val.get('hOverflow')} "
              f"card={val.get('card')} sidebar={val.get('sidebar')} topbar={val.get('topbar')}")
        shot = cmd("Page.captureScreenshot", {"format": "png"})
        with open(os.path.join(SHOTS, f"cdp-{label}.png"), "wb") as f:
            f.write(base64.b64decode(shot["data"]))
    except Exception as e:
        print(f"{label}: ERROR {type(e).__name__}: {e}")
    finally:
        if ws:
            try: ws.close()
            except Exception: pass
        proc.kill()

def main():
    tok = json.load(open(r"E:\Local\projects\personal-assistant\data\.ken_tokens.json"))["owner"]
    cases = [
        (360, 780, "login360", None),
        (412, 915, "login412", None),
        (430, 932, "login430", None),
        (360, 780, "app360", tok),
        (412, 915, "app412", tok),
        (430, 932, "app430", tok),
        (768, 1024, "app768", tok),
    ]
    for w, h, label, tokv in cases:
        audit(free_port(), "http://localhost:8756/", w, h, label, token=tokv)
        time.sleep(1)

if __name__ == "__main__":
    main()
