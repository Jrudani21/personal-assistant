"""Drive KEN in a simulated iPhone (Playwright WebKit) and report what breaks.

Chrome could not reproduce the "renders but nothing is clickable" report from
the real phone. WebKit is the engine iOS Safari actually uses, so this is the
closest reproduction available without the device.

    py -3.12 test_iphone.py                 # tailnet URL, owner token
    py -3.12 test_iphone.py http://127.0.0.1:8756/
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

import server

DEFAULT_URL = f"https://janak.tailda4f11.ts.net/ken/?token={server.ACCESS_TOKEN}"
SHOT_DIR = Path(__file__).resolve().parent / "data" / "iphone_shots"


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    console: list[str] = []
    page_errors: list[str] = []
    failed: list[str] = []

    with sync_playwright() as p:
        browser = p.webkit.launch()
        ctx = browser.new_context(
            **p.devices["iPhone 14 Pro"],
            ignore_https_errors=True,
        )
        page = ctx.new_page()
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed",
                lambda r: failed.append(f"{r.method} {r.url} -> {r.failure}"))

        print(f"loading {url.split('?')[0]} ...")
        page.goto(url, wait_until="load", timeout=45000)
        page.wait_for_timeout(5000)

        print("\n=== PAGE ERRORS ===")
        print("\n".join(page_errors) if page_errors else "  none")
        print("\n=== CONSOLE (errors/warnings) ===")
        noisy = [c for c in console if c.startswith(("[error]", "[warning]"))]
        print("\n".join(noisy[:15]) if noisy else "  none")
        print("\n=== FAILED REQUESTS ===")
        print("\n".join(failed[:10]) if failed else "  none")

        def probe(label: str, expr: str):
            try:
                print(f"  {label}: {page.evaluate(expr)}")
            except Exception as exc:
                print(f"  {label}: EVAL FAILED - {exc}")

        print("\n=== STATE ===")
        probe("title", "document.title")
        probe("script ran (BASE defined)", "typeof BASE !== 'undefined' ? BASE : 'UNDEFINED'")
        probe("handlers wired", "!!(document.getElementById('brainBtn') && document.getElementById('brainBtn').onclick)")
        probe("nav buttons", "document.querySelectorAll('.navbtn').length")
        probe("error banner", "document.getElementById('jsErrBar') ? document.getElementById('jsErrBar').textContent.slice(0,300) : 'none'")
        probe("HUD", "document.getElementById('hud').textContent")
        probe("viewport", "window.innerWidth + 'x' + window.innerHeight")
        page.screenshot(path=str(SHOT_DIR / "01_idle.png"))

        print("\n=== TAP 'Brain' (real touch event) ===")
        try:
            page.tap("#brainBtn")
            page.wait_for_timeout(3000)
            probe("brain panel open", "document.getElementById('brainScrim').classList.contains('open')")
            probe("vault notes", "document.querySelectorAll('#vaultList .vault-note').length")
            page.screenshot(path=str(SHOT_DIR / "02_brain.png"))
        except Exception as exc:
            print(f"  TAP FAILED: {exc}")

        print("\n=== TAP a note ===")
        try:
            page.tap("#vaultList .vault-note")
            page.wait_for_timeout(2000)
            probe("note chars", "document.getElementById('vaultReader').textContent.length")
            page.screenshot(path=str(SHOT_DIR / "03_note.png"))
        except Exception as exc:
            print(f"  TAP FAILED: {exc}")

        print("\n=== TYPE in the composer ===")
        try:
            page.tap("[data-close=brainScrim]")
            page.wait_for_timeout(800)
            page.tap("#input")
            page.fill("#input", "hello from webkit")
            probe("input value", "document.getElementById('input').value")
            page.screenshot(path=str(SHOT_DIR / "04_typed.png"))
        except Exception as exc:
            print(f"  INPUT FAILED: {exc}")

        if page_errors:
            print("\n=== LATE PAGE ERRORS ===")
            print("\n".join(page_errors))

        browser.close()

    print(f"\nscreenshots -> {SHOT_DIR}")
    return 1 if page_errors else 0


if __name__ == "__main__":
    sys.exit(main())
