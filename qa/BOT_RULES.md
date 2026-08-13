# KEN Bot Rules & Regulations (v1)

Every bot/agent that runs against KEN (QA bots, fix crews, cron jobs, sandbox
instances) MUST follow these rules. Violations get the bot's access revoked.

## 1. Scope of action (what bots may do)

| Area | Allowed | Forbidden |
|---|---|---|
| Sandbox (:8766) | Everything: run tests, create users, break things, restart | — |
| Live service (:8756) | Read-only: health, login as test user, GET endpoints | Changing live data (chats, memory, users), restarting outside the fix crew |
| data/ (live) | Read logs | Write/modify/delete (real user data) |
| Source code (server.py, ken.html, qa/) | Fix crew only | Test bots / QA bots (they only report) |
| External calls | DeepSeek API via KEN (billed) | Unbilled/unknown API calls; scraping |

## 2. Token/credit budget (save tokens — the whole point)

- **Test bots are DETERMINISTIC (no LLM)**. Playwright + API probes only.
  Never call an LLM from a test bot.
- **Fix crew runs ONLY in off-peak hours** (03:00–06:00 local) and ONLY when
  `offpeak_fix.py` reports OPEN_BUGS. Never fire the crew speculatively.
- One fix attempt per bug per day. If the fix fails, log it and escalate, do
  not retry endlessly.
- The nightly QA job re-runs the suite; if green, NO LLM work at all.

## 3. Escalation rule (bots → crew → human)

```
bot detects failure ──► logs to data/qa_buglog.jsonl (free, deterministic)
      │
      ├─ new failure ──► off-peak fix crew (LLM, scheduled 03:00) reads work order,
      │                   fixes, marks fixed, re-runs suite
      │
      └─ fix fails / ambiguous ──► CREW asks the HUMAN (via chat) with:
                                  - the failing test name + error
                                  - what it tried
                                  - the specific question it can't resolve
```

- A bot that hits something it can't resolve MUST ask for help — it must NOT
  guess, force changes, or silently retry forever.
- The fix crew, in turn, can delegate sub-tasks to other agents (tester,
  frontend dev, backend dev) but the final "is it fixed" gate is always the
  deterministic Playwright suite.
- Never escalate for known/expected failures (dedup by fingerprint).

## 4. Sandbox hygiene

- Tests always run against the sandbox, never live.
- The sandbox is disposable: wipe its data dir freely, restart freely.
- One sandbox per port; do not start duplicate instances.

## 5. Safety invariants (never violated)

- No bot may ever: delete live user data, send external messages, spend money
  (beyond normal DeepSeek usage by the fix crew), expose the API key.
- The API key stays on the server; bots never read/print it.
- Anything destructive (real-money orders, live deletes, funnel changes)
  requires a human confirm — bots never do these.

## 5b. Phone approval gate (owner-in-the-loop)

Bots flagged `needs_approval: true` in `data/bots.json` NEVER auto-run:
- The scheduler emits `PENDING_APPROVAL <id>: <reason>`.
- The agent delivers that request to the owner's phone (Telegram/WhatsApp
  once a channel is wired) and WAITS for an explicit yes/no reply.
- Approved → the bot runs. Denied → it's skipped and logged to
  `data/approvals.jsonl`. No reply → stays pending, never runs.
- This is the mechanism for any bot touching money, live data, or the funnel.
  (Example flag: `"needs_approval": true, "approval_note": "..."`.)

## 6. Reporting

- Every bot run appends one line to data/qa_buglog.jsonl (structured).
- The off-peak crew's work order + outcome is the daily "bug report".
- If the human isn't reachable, the crew leaves the bug OPEN and moves on.
