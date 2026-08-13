#!/usr/bin/env python
"""Verify ken.html after the 3D edit: CRLF integrity + extract inline JS for node --check."""
import re
from pathlib import Path

P = Path(r"E:/Local/projects/personal-assistant/ken.html")
with open(P, "r", encoding="utf-8", newline="") as f:
    data = f.read()
crlf = data.count("\r\n")
lf = data.count("\n")
print(f"lines: {lf}, CRLF: {crlf}, lone LF: {lf - crlf} (must be 0)")

# main inline script (the one right after the <script src=...> tag)
m = re.search(r'<script>\s*"use strict";(.*?)</script>', data, re.S)
if not m:
    print("FAIL: could not find main inline script")
    raise SystemExit(1)
out = Path(r"C:/Users/Janak's PC/AppData/Local/Temp/ken_app.js")
out.write_text('"use strict";\n' + m.group(1), encoding="utf-8", newline="")
print(f"inline JS extracted: {len(m.group(1))} chars -> {out}")

# sanity anchors
for needle in ['src="assets/three.min.js"', "window.KEN3D", "new THREE.WebGLRenderer",
               "powerPreference: \"high-performance\"", ".idle-view > *:not(canvas)",
               "idleView.insertBefore(wrap, idleView.firstChild)", "id=\"ken3dWrap\"",
               "id=\"minimizeBtn\"", "id=\"fsBtn\"", "id=\"kenWidget\""]:
    print(f"  {'OK ' if needle in data else 'MISSING'} {needle!r}")
