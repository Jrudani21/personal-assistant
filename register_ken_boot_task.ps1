# register_ken_boot_task.ps1
# Registers/updates JanakKEN to start at boot as the interactive user.
#
# Why not SYSTEM? KEN exposes run_python and admin tools, so a compromise
# would become SYSTEM-level code execution. It runs as the interactive user
# with RunLevel Limited, requiring a stored password (LogonType Password).
#
# Why both AtStartup and AtLogOn? AtStartup brings it up at boot without
# anyone logging in. AtLogOn recovers if it was ever stopped while logged in.
#
# Why the readiness poll? A single fixed sleep gives false failures on slow
# starts. Polling at 1s intervals up to 30 times is more reliable.
#
# ASCII only: Windows PowerShell 5.1 misreads UTF-8-without-BOM scripts
# containing multi-byte characters and fails with a bogus "string is missing
# the terminator" error.

# param() must be the FIRST statement in the script. If anything (even an
# assignment) precedes it, PowerShell parses it as a call to a command named
# "param" instead of a parameter block: the script still parses cleanly but
# dies at runtime with "The term 'param' is not recognized".
param(
    [string]$Pythonw = "C:\Users\Janak's PC\AppData\Local\Programs\Python\Python312\pythonw.exe"
)

$ErrorActionPreference = "Stop"

$TaskName = "JanakKEN"
$RepoRoot = $PSScriptRoot
$Port = 8756
$HealthUrl = "http://127.0.0.1:$Port/api/health"

# --- Elevation check ---------------------------------------------------------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

# --- Preconditions -----------------------------------------------------------
if (-not (Test-Path $Pythonw)) {
    Write-Error "pythonw.exe not found at: $Pythonw"
    exit 1
}
if (-not (Test-Path (Join-Path $RepoRoot "ken_service.py"))) {
    Write-Error "ken_service.py not found in repo: $RepoRoot"
    exit 1
}
if (-not (Test-Path (Join-Path $RepoRoot "assistant"))) {
    Write-Error "assistant package not found in repo: $RepoRoot"
    exit 1
}

# --- Key placement -----------------------------------------------------------
# Copy the key into the repo if it is not already there, so FILE 1's option 3
# works regardless of profile state.
$RepoKeyPath = Join-Path $RepoRoot "data\.deepseek_key"
$HomeKeyPath = Join-Path $HOME ".deepseek_key"
$copiedKey = $false
if (-not (Test-Path $RepoKeyPath) -and (Test-Path $HomeKeyPath)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $RepoKeyPath) | Out-Null
    Copy-Item $HomeKeyPath $RepoKeyPath
    $copiedKey = $true
}
if ($copiedKey) {
    Write-Host "Copied .deepseek_key from HOME to repo data directory."
} else {
    Write-Host "Key file check: repo key exists or no HOME key to copy. (Key content not shown.)"
}

# --- Prompt for password (never echo, never logged) --------------------------
# Two different secrets are easy to confuse here:
#   * this prompt = your WINDOWS ACCOUNT password. Windows stores it in its own
#     credential vault so it can start the task at boot as you rather than as
#     SYSTEM. KEN never sees it.
#   * the KEN ACCESS TOKEN = what you type in the browser on your phone. It is
#     in data/.ken_tokens.json and is printed at the end of this script.
Write-Host ""
Write-Host "Enter the WINDOWS ACCOUNT password for $env:USERDOMAIN\$env:USERNAME"
Write-Host "(the one you use to sign in to this PC - NOT the KEN access token)"
Write-Host "Windows needs it to start KEN at boot without you logging in."
Write-Host "It is stored by Windows, not by KEN, and is never shown or logged."
$securePassword = Read-Host -AsSecureString -Prompt "Windows password"
if ($securePassword.Length -eq 0) {
    Write-Error "Password cannot be empty."
    exit 1
}

# Convert to plaintext ONLY for the Register-ScheduledTask call.
$passwordBSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$passwordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($passwordBSTR)

try {
    # --- Build scheduled task pieces -----------------------------------------
    $action = New-ScheduledTaskAction -Execute $Pythonw `
        -Argument "`"$(Join-Path $RepoRoot 'ken_service.py')`"" `
        -WorkingDirectory $RepoRoot

    $triggerStartup = New-ScheduledTaskTrigger -AtStartup
    $triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew

    $taskPrincipal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Password `
        -RunLevel Limited

    # --- Register or update ---------------------------------------------------
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Write-Host "Task $TaskName exists - updating it." -ForegroundColor Yellow
        Set-ScheduledTask -TaskName $TaskName `
            -Action $action `
            -Trigger $triggerStartup, $triggerLogon `
            -Settings $settings `
            -Principal $taskPrincipal `
            -Password $passwordPlain | Out-Null
    } else {
        Register-ScheduledTask -TaskName $TaskName `
            -Action $action `
            -Trigger $triggerStartup, $triggerLogon `
            -Settings $settings `
            -Principal $taskPrincipal `
            -Password $passwordPlain `
            -Description "KEN personal assistant web app (FastAPI on 127.0.0.1:$Port). Started at boot and logon." | Out-Null
        Write-Host "Registered $TaskName." -ForegroundColor Green
    }
}
finally {
    # Zero and free the BSTR, regardless of success/failure.
    if ($passwordBSTR -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordBSTR)
    }
}

# --- Start and verify ---------------------------------------------------------
Write-Host "`nStarting task now..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $TaskName

Write-Host "Polling $HealthUrl for readiness (up to 30s)..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        $ready = $true
        break
    } catch {
        # Not ready yet, keep polling.
    }
}

$taskInfo = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "`nLastTaskResult: $($taskInfo.LastTaskResult)"
Write-Host "Note: 267009 means 'still running' and is expected."

if ($ready) {
    Write-Host "KEN is UP - model $($health.model), provider $($health.provider)." -ForegroundColor Green
} else {
    Write-Host "KEN did not answer on :$Port within 30 seconds." -ForegroundColor Yellow
    Write-Host "Check with: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
}

Write-Host "`nIMPORTANT: If the Windows account password changes, the stored"
Write-Host "password becomes stale and the task will silently stop starting."
Write-Host "Re-run this script after any password change."
Write-Host "`nIt will now start automatically at boot and at logon."
Write-Host "Stop/disable:  Stop-ScheduledTask -TaskName $TaskName   /   Disable-ScheduledTask -TaskName $TaskName"

# --- Show the KEN access link ------------------------------------------------
# Different secret from the Windows password above: this is what you type in
# the browser. Printed here so setup ends with the thing you actually need.
Write-Host "`n--- Your KEN access link (open this on your phone) ---" -ForegroundColor Cyan
try {
    $py = Join-Path (Split-Path $Pythonw) "python.exe"
    if (-not (Test-Path $py)) { $py = "py" }
    Push-Location $RepoRoot
    & $py "ken_share.py" url
    Pop-Location
} catch {
    Write-Host "Could not read the token. Run this yourself:" -ForegroundColor Yellow
    Write-Host "  py -3.12 ken_share.py url"
}
Write-Host "`nOpen that link once per device; it is exchanged for a cookie and"
Write-Host "drops out of the address bar. Share a tester link instead with:"
Write-Host "  py -3.12 ken_share.py invite <name> --hours 24"
