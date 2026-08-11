# SPEC — live reload for KEN

Two deliverables, each in its own fenced block, in this order:

1. A Python block: the code to add to `server.py`.
2. A JavaScript block: the code to add to `ken.html`'s module script.

No commentary outside the blocks.

## Goal

When `ken.html` is edited, or the server restarts, every open browser (phone
included) reloads itself within a few seconds. No manual refresh, ever.

This also defends against the bug that motivated it: iOS Safari served a
stale cached `ken.html` for hours, so shipped fixes never reached the device
and the app looked permanently broken.

## Part 1 — `server.py`

Add a **build id** and an SSE endpoint.

- `BUILD_ID` computation: a short string that changes when either
  (a) `ken.html`'s mtime+size changes, or (b) the server process restarts.
  Combine an md5 of `f"{stat.st_mtime_ns}:{stat.st_size}"` for `ken.html`
  with a per-process `uuid4().hex[:8]` generated at import. Provide
  `_build_id() -> str` that recomputes the file part on each call so an edit
  is picked up without a restart. If `ken.html` is missing, fall back to the
  process id alone rather than raising.

- `GET /api/livereload` returning `EventSourceResponse` (sse_starlette is
  already imported). Loop:
  - emit `event: build`, `data: {"build": "<id>"}` immediately, then
  - poll every 1.0s via `await asyncio.sleep(1.0)`; whenever `_build_id()`
    differs from the last emitted value, emit another `build` event.
  - `ping=15` like the other SSE endpoints, so proxies keep it open.
  - The generator must exit cleanly on `asyncio.CancelledError` (client
    navigating away cancels it) without logging a traceback.
  - **Never block the event loop**: `_build_id()` does a stat, which is
    cheap, but call it via `asyncio.to_thread` to stay consistent with the
    other endpoints in this file.

- Add `"GET  /api/livereload"` to the endpoint list in the `/` route's JSON.

- Auth: leave it to the existing middleware (do NOT add it to `_AUTH_EXEMPT`).
  It must require a token like everything else.

## Part 2 — `ken.html`

Add a self-contained block. Assume `BASE` (path prefix, e.g. `/ken`), the
`url(path)` helper, `toast(msg)`, and `report(payload)` already exist.

- Open `new EventSource(url("/api/livereload"))`.
- On the first `build` event, record the id. On any later `build` event with
  a **different** id, call `location.reload()`.
- Before reloading, show `toast("Updated - reloading...")` and wait ~400ms so
  the toast is visible rather than flashing.
- Reconnect on error with backoff (2s, doubling, 30s cap). `EventSource`
  auto-reconnects, but an outright failure (server restarting) must not leave
  the page permanently disconnected — close and re-open on `onerror` after
  the backoff.
- **Only reload when the document is visible.** If `document.hidden`, set a
  flag and reload on the next `visibilitychange` instead — a phone waking a
  background tab shouldn't fight the user.
- Guard the whole thing in try/catch; live reload failing must never break
  the app.
- One short comment explaining why it exists (stale-cache defence), not how
  EventSource works.

## Constraints

- Python 3.12, existing imports only (`asyncio`, `hashlib`, `uuid`, `json`,
  `EventSourceResponse` are all available in server.py already).
- Plain ES2017 JS, no build step, no external libraries.
- Must work under the `/ken` path prefix, so all URLs go through `url()`.
