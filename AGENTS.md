# AGENTS.md - KEN personal assistant

Local FastAPI + web UI assistant ("KEN") plus the bot-fleet runtime in `qa/`. `README.md` is
partly stale (Streamlit/Ollama era); trust `server.py`, `app.py`, `API.md`,
`KEN_BUILD_SPEC.md` and these docs.

## Setup

- Python 3.12 via `py -3.12` (no venv in the repo). Strip the Hermes/Ken env leak:
  `env -u PYTHONPATH -u VIRTUAL_ENV -u PYTHONHOME py -3.12 ...`.
- Deps: `requirements.txt` (assistant), `requirements-server.txt` (FastAPI/uvicorn/SSE).
  Don't install or upgrade without asking.
- Keys come from env or the Hermes/Ken `.env`; never print them.

## Run

- Dev: `py -3.12 -m uvicorn server:app --port 8756 --reload` or `py -3.12 app.py`.
- Experiments go to the **sandbox**: `python sandbox.py start --port 8766`
  (`qa/BOT_RULES.md`: the live service on :8756 is read-only for bots).
- The live service is a scheduled task (`ken_service.py`); restarting it affects the owner.

## Test

```
PYTHONDONTWRITEBYTECODE=1 env -u PYTHONPATH -u VIRTUAL_ENV -u PYTHONHOME \
  py -3.12 -X utf8 -m pytest tests -p no:cacheprovider -q
```
Expect 229 passed (2026-10-15). Keep new tests offline (mock providers; no LM Studio, no
DeepSeek).

## Conventions

- Don't edit `assistant/*` to suit the server; add behaviour in `server.py` or add a tested
  function in `assistant/`.
- New tools: register in `assistant/tools.py`; anything that executes code, touches files,
  or deletes data must default OFF and be added to `GUEST_BLOCKED_TOOLS`.
- New API routes that change settings, sharing or access must be added to
  `GUEST_BLOCKED_PATH_PREFIXES`.
- Settings via `assistant/config.py`; env knobs prefixed `KEN_`.
- Preserve each file's line endings (`ken.html` is CRLF in the working tree); run
  `verify_ken3d.py` after large `ken.html` edits.

## Do / don't

- **Never write to live `data/`** (chats, memory, users, tokens) from tests or scripts; use
  `tmp_path`/monkeypatch or the sandbox.
- Never commit anything under `data/*`, including `data/memory.json` (personal profile facts; untracked and gitignored since 2026-10-16)
  casually: the repo is public.
- Don't run `ken_share.py` funnel commands, `register_ken_*.ps1`, or `qa/` bots unless asked.
- Don't hit the local GPU (LM Studio) for bulk work.
- No AI co-author trailers in commits. Let the gitleaks pre-commit hook run.

## Sensitive paths

`server.py` (auth, guest blocking, rate guard), `data/.ken_tokens.json`, `data/.ken_users.json`,
`data/memory.json`, `assistant/deepseek_key.py`, `ken_share.py`, `desktop/main.js` (token
injection), `qa/BOT_RULES.md`.

## Verified against

`server.py`, `app.py`, `sandbox.py`, `ken_service.py`, `qa/BOT_RULES.md`,
`KEN_BUILD_SPEC.md`, `.gitignore`, `.githooks/pre-commit`, pytest run above.
