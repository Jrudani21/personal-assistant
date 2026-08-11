# SPEC — rewrite the live-reload client block (JavaScript only)

Output ONE fenced javascript block. No commentary.

Your previous attempt had broken reconnection. Concretely:

- `ls.onerror` closed `ls`, created `newLs`, and attached `ls._buildHandler`
  to it - but never reassigned `ls`. So on the next failure the handler
  closed the ALREADY-CLOSED original and spawned yet another EventSource.
  Sockets leak and the reconnect chain breaks after the second failure.
- `ls._buildHandler` was referenced inside `ls.onerror` before it was
  assigned, so the first reconnect attached `undefined` as the listener.
- The build-event handler logic was written out twice and could drift.
- `reconnectAttempts` never reset after a successful reconnect, so one blip
  early on permanently pinned the delay at 30s.

Requirements:

- A single `connect()` function that creates the EventSource, wires its
  handlers, and is called again (after the backoff) on failure. Exactly one
  live EventSource at a time: always `close()` the previous before opening a
  new one, and keep the reference in one variable that `connect()` reassigns.
- One build-event handler, defined once.
- Backoff 2s doubling to a 30s cap, reset to 2s once a connection succeeds
  (use the EventSource `open` event to detect success).
- First `build` event records the id; any later `build` with a different id
  triggers reload.
- If `document.hidden`, defer: set a flag and reload on the next
  `visibilitychange` when visible. Register that listener once, not per
  connection.
- Before reloading: `toast("Updated - reloading...")`, then reload after
  ~400ms.
- Whole block wrapped in try/catch; a live-reload failure must never break
  the app.
- Assume `url(path)` and `toast(msg)` exist. Plain ES2017, no libraries.
- One short comment on WHY it exists (stale-cache defence). Do not narrate
  how EventSource works.
