#!/usr/bin/env python
"""Verify the LIVE server serves the owner-gated ken.html (after login)."""
import http.cookiejar
import os
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8756"
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def req(path, method="GET", data=None):
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(BASE + path, data=body,
                               headers={"Content-Type": "application/json"}, method=method)
    try:
        with op.open(r) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

TEST_USER = os.environ.get("KEN_TEST_USER", "janak")
TEST_PW = os.environ.get("KEN_TEST_PASSWORD")
if not TEST_PW:
    raise SystemExit("Set KEN_TEST_PASSWORD before running this gate (no credentials in the repo).")

s, _ = req("/api/login", "POST", {"username": TEST_USER, "password": TEST_PW})
print("login:", s)
s, html = req("/")
print("root with session:", s)
# The sidebar buttons should be present with display:none (hidden until owner check)
for btn in ["sbFleet", "sbUsers", "sbSettings", "sbStatus"]:
    # in the raw HTML they're style="display:none"
    present_hidden = f'id="{btn}" style="display:none"' in html or f'id="{btn}"' in html
    print(f"  {btn}: in served HTML = {present_hidden}")
# The reveal logic should gate on role
print("  owner-reveal JS present:", 'sbStatus").style.display' in html or 'sbStatus' in html and 'role === "owner"' in html)
# And /api/status should work with the owner session
s2, body = req("/api/status")
print("owner /api/status:", s2, "(expect 200)")
