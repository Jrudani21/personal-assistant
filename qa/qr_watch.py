#!/usr/bin/env python
"""Watch the WhatsApp bridge's --pair-json stdout and regenerate the QR PNG
whenever a new one appears (QR rotates ~every 20s)."""
import re
import struct
import subprocess
import time
import zlib
from pathlib import Path

BRIDGE = r"C:/Users/Janak's PC/AppData/Local/hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js"
SESSION = r"C:/Users/Janak's PC/AppData/Local/hermes/whatsapp/session"
OUT = Path(r"E:/Local/projects/personal-assistant/assets/whatsapp_qr.png")


def render(qr_str: str):
    import qrcode
    qr = qrcode.QRCode(border=2, box_size=10)
    qr.add_data(qr_str)
    qr.make(fit=True)
    mat = qr.get_matrix()
    n = len(mat)
    size = n * 10
    rows = []
    for ry in range(n):
        for _ in range(10):
            row = bytearray()
            for rx in range(n):
                v = 0 if mat[ry][rx] else 255
                row += bytes([v, v, v]) * 10
            rows.append(bytes(row))
    raw = b"".join(b"\x00" + r for r in rows)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    OUT.write_bytes(png)
    print(f"[{time.strftime('%H:%M:%S')}] wrote new QR ({size}x{size})")


proc = subprocess.Popen(
    ["node", BRIDGE, "--pair-json", "--session", SESSION],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
seen = set()
for line in proc.stdout:
    line = line.strip()
    m = re.search(r'"event":"qr","qr":"([^"]+)"', line)
    if m and m.group(1) not in seen:
        seen.add(m.group(1))
        try:
            render(m.group(1))
        except Exception as e:
            print("render err:", e)
