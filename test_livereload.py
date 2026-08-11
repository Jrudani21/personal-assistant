"""Prove live reload works: open KEN in WebKit, edit ken.html, expect a reload.

The page must reload itself with no interaction — that is the whole point
(refreshing a phone is awkward, and a stale cached page previously hid every
shipped fix).

    py -3.12 test_livereload.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

import server

BASE = Path(__file__).resolve().parent
KEN_HTML = BASE / "ken.html"
URL = f"https://janak.tailda4f11.ts.net/ken/?token={server.ACCESS_TOKEN}"


def main() -> int:
    original = KEN_HTML.read_bytes()
    reloads: list[float] = []
    errors: list[str] = []
    ok = False

    try:
        with sync_playwright() as p:
            browser = p.webkit.launch()
            ctx = browser.new_context(**p.devices["iPhone 14 Pro"],
                                      ignore_https_errors=True)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            # every commited navigation = one page load
            page.on("load", lambda _: reloads.append(time.time()))

            print("1. loading page...")
            page.goto(URL, wait_until="load", timeout=45000)
            page.wait_for_timeout(4000)
            first = len(reloads)
            print(f"   loads so far: {first}")
            # Verify a REAL open connection to our endpoint, not merely that
            # the browser supports EventSource -- the earlier version of this
            # check tested `typeof EventSource !== "undefined"`, which is a
            # capability probe that passes even when the client code is
            # missing entirely. It hid exactly that failure.
            connected = page.evaluate("""
                new Promise((resolve) => {
                    const es = new EventSource(
                        location.pathname.replace(/\\/+$/, "") + "/api/livereload");
                    const done = (v) => { es.close(); resolve(v); };
                    es.addEventListener("build", () => done("receiving build events"));
                    es.onerror = () => done("ERROR: could not connect");
                    setTimeout(() => done("TIMEOUT: no event in 8s"), 8000);
                })
            """)
            print(f"   livereload endpoint: {connected}")
            has_client = page.evaluate(
                "document.documentElement.innerHTML.includes('api/livereload')")
            print(f"   client block present in page: {has_client}")

            print("2. touching ken.html (simulating an edit)...")
            KEN_HTML.write_bytes(original + b"\n<!-- livereload test -->\n")

            print("3. waiting up to 20s for an automatic reload...")
            deadline = time.time() + 20
            while time.time() < deadline:
                if len(reloads) > first:
                    ok = True
                    break
                page.wait_for_timeout(500)

            elapsed = (reloads[-1] - reloads[first - 1]) if ok else None
            print(f"   reloaded automatically: {ok}"
                  + (f" (after {elapsed:.1f}s)" if elapsed else ""))

            if ok:
                page.wait_for_timeout(2500)
                wired = page.evaluate(
                    "!!(document.getElementById('brainBtn') && "
                    "document.getElementById('brainBtn').onclick)")
                print(f"   app still functional after reload: {wired}")
                ok = ok and wired

            print(f"\npage errors: {errors if errors else 'none'}")
            browser.close()
    finally:
        KEN_HTML.write_bytes(original)
        print("restored ken.html")

    print("\nRESULT:", "PASS" if ok and not errors else "FAIL")
    return 0 if ok and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
