#!/usr/bin/env python
"""Wait for WhatsApp 429 rate-limit reset, then start the bridge once and
print the pairing code. Run with: python qa/wa_pair.py"""
import re
import subprocess
import sys
import time

BRIDGE = r"C:/Users/Janak's PC/AppData/Local/hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js"
SESSION = r"C:/Users/Janak's PC/AppData/Local/hermes/whatsapp/session"
PHONE = "12044421730"  # E.164 digits only, with country code

print("Waiting 20 min for WhatsApp 429 rate-limit reset...")
time.sleep(20 * 60)

print("Starting bridge...")
proc = subprocess.Popen(
    ["node", BRIDGE, "--pair-code", PHONE, "--session", SESSION],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
for line in proc.stdout:
    line = line.strip()
    print(line, flush=True)
    m = re.search(r'([A-Z0-9]{4}-[A-Z0-9]{4})', line)
    if m:
        print(f"\n=== PAIRING CODE: {m.group(1)} ===")
        print("Enter in WhatsApp → Settings → Linked devices → Link with phone number instead")
        print("Phone: +1 204 442 1730")
        sys.stdout.flush()
        break
