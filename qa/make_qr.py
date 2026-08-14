#!/usr/bin/env python
"""Render a WhatsApp pairing QR to PNG WITHOUT PIL (pure zlib + struct)."""
import qrcode
import struct
import sys
import zlib

QR_STR = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else "whatsapp_qr.png"

qr = qrcode.QRCode(border=2, box_size=10)
qr.add_data(QR_STR)
qr.make(fit=True)
mat = qr.get_matrix()  # list of lists of bool
n = len(mat)
size = n * 10

# Build raw RGB rows (white bg, black modules)
rows = []
for ry in range(n):
    for _ in range(10):
        row = bytearray()
        for rx in range(n):
            val = 0 if mat[ry][rx] else 255
            row += bytes([val, val, val]) * 10
        rows.append(bytes(row))

raw = b"".join(b"\x00" + r for r in rows)  # filter byte 0 per row


def chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(raw, 9))
png += chunk(b"IEND", b"")

with open(OUT, "wb") as f:
    f.write(png)
print(f"saved {OUT} ({size}x{size})")
