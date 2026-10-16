# Testing - KEN personal assistant

## Suites

| Suite | Location | Runner |
|---|---|---|
| Unit/integration (offline, mocked LLMs) | `tests/` (14 `test_*.py` + `conftest.py`) | pytest (`pytest>=8.0` in `requirements.txt`) |
| Root-level scripted checks | `test_iphone.py` (Playwright WebKit iPhone run), `test_livereload.py` (edits `ken.html`, expects reload), `verify_local_tier.py`, `verify_ken3d.py`, `graph_verify_final.py` | run directly; need a live server / LM Studio |
| QA bots | `qa/qa_runner.py` (deterministic hourly), `qa/tests/` (Playwright specs: chat, login, sidebar, threejs) with `qa/playwright.config.js`, `qa_crew.py` | scheduled or manual against the sandbox |

`tests/` covers: auth, backup, crew (+cache, tools, numeric-consistency guardrail), DeepSeek
key lookup, local LLM transport, structured memory, sessions JSONL + distill, reminder daemon,
deep-analysis tool, vault parsing. All files use `monkeypatch`/fakes; `conftest.py` writes a
temporary CSV into `data/workspace/` and deletes it.

## Verified on 2026-10-15

```
PYTHONDONTWRITEBYTECODE=1 env -u PYTHONPATH -u VIRTUAL_ENV -u PYTHONHOME \
  py -3.12 -X utf8 -m pytest tests -p no:cacheprovider -q -x --timeout=60
```
Result: **229 passed, 104 warnings in 14.95 s** (warnings are CrewAI deprecations).
`--collect-only` first: 229 tests collected. `data/memory.json` checksum and `git status`
unchanged afterwards. No network or GPU use observed (all model calls are mocked).

## Not run

Root-level `test_iphone.py`, `test_livereload.py` (mutates `ken.html`), `verify_local_tier.py`
(hits LM Studio on the local GPU), `qa/` bots (touch live data / services).

## Gaps

- No CI (no `.github/`).
- `server.py` (about 2,400 lines, the HTTP/SSE layer and tool loop) has limited test
  coverage (`tests/test_auth.py`); the provider fallback chain has no test.
- `fetch_webpage` and other tools in `assistant/tools.py` have no unit tests for URL handling.
- Default `pytest` (without `-p no:cacheprovider`) writes `.pytest_cache/`, which is not
  gitignored.

## Verified against

`ls tests`, `tests/conftest.py`, `grep -L "monkeypatch|mock|Fake|stub" tests/test_*.py` (none),
root script docstrings, commands above (executed), `.gitignore`.
