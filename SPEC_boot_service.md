# SPEC — run KEN at boot, before/without user logon

Two files. Output each complete, in its own fenced block, in the order given.
No commentary.

## Background

KEN currently runs as scheduled task `JanakKEN` with `-AtLogOn` and
`LogonType Interactive`. That stops KEN whenever the user logs off, and never
starts it if nobody logs on. Goal: run at boot, independent of logon.

Deliberately NOT running as SYSTEM: KEN exposes `run_python` and `admin`
tools, so a compromise would become SYSTEM-level code execution. It must keep
running as the interactive user with `RunLevel Limited`, which requires a
stored password (`LogonType Password`).

---

## FILE 1 — `assistant/deepseek_key.py` (new)

Single source of truth for locating the DeepSeek API key.

**Why this is needed:** `server.py` currently does
`(Path.home() / ".deepseek_key").read_text()`. When a task runs under
`LogonType Password` at boot, the user profile may not be loaded and
`Path.home()` can resolve somewhere unexpected, so the key silently goes
missing and KEN refuses to start with no obvious cause.

Provide `find_key() -> str` returning the key or `""`. Resolution order,
first non-empty wins:

1. `DEEPSEEK_API_KEY` env var
2. file named by `KEN_DEEPSEEK_KEY_FILE` env var
3. `<repo>/data/.deepseek_key`   (repo root = parent of the `assistant` package)
4. `Path.home() / ".deepseek_key"`
5. `C:/Users/Janak's PC/.deepseek_key`  — explicit absolute fallback for the
   profile-not-loaded case

Strip whitespace. Every filesystem read wrapped so a missing/unreadable file
is skipped, never raised. Also provide `key_source() -> str` returning a short
human-readable label of which of the five won (for logging/diagnostics) —
never the key itself. Stdlib only.

---

## FILE 2 — `register_ken_boot_task.ps1` (new)

Registers/updates `JanakKEN` to start at boot as the interactive user.

Requirements:

- Must abort with a clear message if not elevated.
- Prompt for the password with `Read-Host -AsSecureString`. Never echo it,
  never write it to a log, transcript, or disk. Convert to plaintext only at
  the `Register-ScheduledTask -Password` call (that API requires it), using
  `[Runtime.InteropServices.Marshal]::PtrToStringAuto(SecureStringToBSTR(...))`
  and zero it afterwards with `ZeroFreeBSTR`.
- Principal: `-UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Password
  -RunLevel Limited`.
- Triggers: `-AtStartup` AND `-AtLogOn` for the same user. Both, so it comes
  up at boot and also recovers if it was ever stopped while logged in.
- Action: `pythonw.exe "<repo>\ken_service.py"`, working directory `<repo>`.
  Resolve `<repo>` from `$PSScriptRoot`, not a hardcoded path.
  pythonw path: `C:\Users\Janak's PC\AppData\Local\Programs\Python\Python312\pythonw.exe`,
  overridable via a `-Pythonw` script parameter.
- Settings: `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew`.
- Before registering, copy the key into the repo if it is not already there:
  if `<repo>\data\.deepseek_key` is absent but `$HOME\.deepseek_key` exists,
  copy it (so FILE 1's option 3 works regardless of profile state). Print
  whether it copied. Never print the key.
- After registering: start the task, then poll
  `http://127.0.0.1:8756/api/health` up to 30 times at 1s intervals before
  declaring success or failure — a single fixed sleep gives false failures on
  slow starts.
- Print `LastTaskResult` and explain that `267009` means "still running" and
  is expected.
- Warn plainly: if the Windows account password changes, the stored password
  becomes stale and the task will silently stop starting — re-run this script.

**ASCII only.** No em dashes, smart quotes, or other non-ASCII: Windows
PowerShell 5.1 misreads UTF-8-without-BOM scripts containing multi-byte
characters and fails with a bogus "string is missing the terminator" error.

Use `$ErrorActionPreference = "Stop"`. Comment the non-obvious parts (why not
SYSTEM, why both triggers, why the readiness poll).
