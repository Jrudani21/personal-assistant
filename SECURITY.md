# Security - KEN personal assistant

KEN is a personal assistant that runs on the owner's own PC and can read the owner's notes,
memory and files and call third-party LLM APIs with the owner's keys. It is a single-owner
project, not a hardened multi-tenant service.

## Reporting a problem

Please do not open a public issue for a suspected vulnerability. Contact the owner privately
(GitHub profile) and allow time for a fix.

## What to know before running it

- The server binds to loopback by default. Only expose it to networks you trust; remote
  access is token-based and the tokens are owner-controlled and revocable.
- Secrets come from environment variables or a local, gitignored `.env`; they are never
  committed. `data/` and `backups/` are gitignored. `.githooks/pre-commit` runs gitleaks on
  staged changes.
- Model-facing features that run code, touch files or delete data are off by default.
- Content you send may go to the configured cloud LLM providers.
- Dependencies are not pinned or audited by CI.

Detailed internal security notes are kept privately and are not part of this repository.
