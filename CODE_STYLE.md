# Code style - KEN personal assistant

No linter/formatter config (no `pyproject.toml`, ruff, black, flake8, `.editorconfig`,
`.gitattributes`). Observed conventions:

## Python

- **Python 3.12**, launched as `py -3.12` (docstrings of `server.py`, `app.py`); no project
  venv in the checkout. When started from a Hermes/Ken shell, strip `PYTHONPATH`,
  `VIRTUAL_ENV`, `PYTHONHOME` first (a py3.11 venv leaks otherwise).
- `server.py` **wraps** `assistant/*` and must not modify it ("IMPORT, DO NOT EDIT",
  `KEN_BUILD_SPEC.md` section 2). New server behaviour goes in `server.py`; shared logic goes in
  `assistant/` with tests.
- Modules start with a docstring explaining design and the incident that motivated it (e.g.
  `ken_service.py` on `pythonw` stdout, `server.py` on why its own tool loop exists).
- Paths are module constants built from `Path(__file__).resolve().parent.parent / "data" / ...`.
- Settings flow through `assistant/config.py` (`DEFAULTS` merged with `data/config.json`); do not
  read `data/config.json` directly elsewhere. Env vars use a `KEN_` prefix for server knobs
  (`KEN_MODEL`, `KEN_TOKEN`, `KEN_FALLBACK_MODEL`, ...).
- Fail soft on optional subsystems (provider missing -> next tier; logging/observations never
  break a turn); fail closed on auth.
- `qa/` bots are "deterministic, NO LLM" by default where possible (`qa_runner.py`,
  `provider_health.py`); an LLM is optional for summaries.
- Type hints with built-in generics; `from __future__ import annotations` in 3 of 29
  `assistant/` modules.

## Front end

- `ken.html` is one file with inline CSS/JS; tokens on `:root` (DESIGN_SYSTEM.md). It is stored
  LF in git and checked out CRLF; after large edits run `verify_ken3d.py` (CRLF integrity +
  `node --check` of the inline JS).

## Line endings

No `.gitattributes`: the working tree is mixed (57 files CRLF, 83 LF, 1 mixed per
`git ls-files --eol`). Preserve a file's existing endings when editing.

## Commits

Short imperative subjects, often with a prefix (`fix`, `feat`, `qa`, `security`); no AI
co-author trailers (owner rule). `.githooks/pre-commit` runs gitleaks.

## Verified against

`server.py`, `app.py`, `ken_service.py` docstrings, `KEN_BUILD_SPEC.md`, `assistant/config.py`,
`qa/*.py` docstrings, `verify_ken3d.py`, `git ls-files --eol`, `git log --format=%s`,
absence of lint config files.
