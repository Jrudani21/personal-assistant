#!/usr/bin/env python3
"""Popup catcher — watches for NEW windows appearing on screen and captures them.

A popup is a new top-level window (toast, banner, notification, dialog) that
appears briefly. Poll the Win32 window list every POLL_S; when a window
appears (new hwnd) or disappears (popped-and-gone), immediately:
  - log hwnd / title / class / pid / exe / rect / time to events.jsonl
  - save a full-screen PNG + the window-region PNG

Ignores persistent windows (game, desktop, known shells). Run for a few
minutes while the popup happens, then read events.jsonl + PNGs.

Usage: python qa/screen_catcher.py [minutes] [outdir]
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import PIL.ImageGrab

NOW = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else
           rf"E:\Local\projects\personal-assistant\data\screen_catch_{NOW}")
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace(".", "").isdigit() else 15
POLL_S = 0.25

OUT.mkdir(parents=True, exist_ok=True)
EVENTS = OUT / "events.jsonl"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 260


def exe_of(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(MAX_PATH)
        size = wintypes.DWORD(MAX_PATH)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(h)
    return ""


def visible_windows() -> dict:
    """{hwnd: (title, class, pid, exe, rect)} for visible top-level windows."""
    out = {}

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        # skip cloaked/zero-size windows
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left <= 0 or r.bottom - r.top <= 0:
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        t = title.value
        c = cls.value
        # skip IME/helper windows that flicker constantly
        if c in ("MSCTFIME UI", "IME", "Windows.UI.Core.CoreWindow", "tooltips_class32") and not t:
            return True
        if not t and c in ("Chrome_WidgetWin_0", "Chrome_WidgetWin_1", "ApplicationFrameWindow"):
            return True
        out[hwnd] = (t, c, pid.value, "", (r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    for hwnd, rec in out.items():
        rec = list(rec)
        rec[3] = exe_of(rec[2])
        out[hwnd] = tuple(rec)
    return out


def log_event(kind: str, hwnd: int, rec: tuple) -> None:
    title, cls, pid, exe, rect = rec
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind, "hwnd": hwnd, "title": title, "class": cls,
        "pid": pid, "exe": exe.split("\\")[-1] if exe else "", "rect": list(rect),
    }
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")
    print(f"[{ev['ts']}] {kind}: {ev['title'][:60]!r} cls={cls} exe={ev['exe']} rect={rect}", flush=True)
    # capture: window region + full screen
    try:
        if rect[2] - rect[0] > 10 and rect[3] - rect[1] > 10:
            img = PIL.ImageGrab.grab(bbox=tuple(rect), all_screens=False)
            img.save(OUT / f"{kind}_{hwnd}_{ev['ts'][11:19].replace(':','')}_win.png")
        PIL.ImageGrab.grab().save(
            OUT / f"{kind}_{hwnd}_{ev['ts'][11:19].replace(':','')}_full.png")
    except Exception as e:
        print("  capture err:", e, flush=True)


def main() -> int:
    print(f"watching for popups for {MINUTES} min -> {OUT}", flush=True)
    known: dict = {}
    start = time.time()
    while time.time() - start < MINUTES * 60:
        try:
            cur = visible_windows()
        except Exception:
            time.sleep(POLL_S)
            continue
        for hwnd, rec in cur.items():
            if hwnd not in known:
                # brand-new window — the popup (or a window that just showed)
                if rec[1] != "Progman" and not rec[0].startswith("Program Manager"):
                    log_event("NEW", hwnd, rec)
        for hwnd, rec in known.items():
            if hwnd not in cur:
                # window vanished — popped and gone
                if rec[1] not in ("", "Progman"):
                    log_event("GONE", hwnd, rec)
        known = cur
        time.sleep(POLL_S)
    print(f"done watching; events -> {EVENTS}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
