# Registers KEN as an always-on scheduled task (runs at logon, restarts on failure).
#
# Run as Administrator:
#   right-click -> "Run with PowerShell as Administrator", or from an elevated
#   prompt:  powershell -ExecutionPolicy Bypass -File register_ken_task.ps1
#
# Uses pythonw.exe so no console window appears, and uvicorn directly rather
# than app.py (app.py opens a browser on start, which is wrong for a daemon).
# Keep this file ASCII: Windows PowerShell 5.1 misreads UTF-8-without-BOM
# scripts that contain multi-byte characters.

$ErrorActionPreference = "Stop"

$TaskName = "JanakKEN"
$ProjectDir = "E:\Local\projects\personal-assistant"
$Pythonw = "C:\Users\Janak's PC\AppData\Local\Programs\Python\Python312\pythonw.exe"
$Port = 8756

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Not running as Administrator. Right-click this script -> 'Run with PowerShell as Administrator'."
    exit 1
}
if (-not (Test-Path $Pythonw))     { Write-Error "pythonw.exe not found at $Pythonw"; exit 1 }
if (-not (Test-Path $ProjectDir))  { Write-Error "Project dir not found: $ProjectDir"; exit 1 }

$action = New-ScheduledTaskAction -Execute $Pythonw `
    -Argument "-m uvicorn server:app --host 127.0.0.1 --port $Port" `
    -WorkingDirectory $ProjectDir

# At logon rather than at startup: KEN reads ~/.deepseek_key and the user's
# data/ directory, so it wants the user's profile to exist.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$taskPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Task $TaskName exists - updating it." -ForegroundColor Yellow
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $taskPrincipal | Out-Null
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $taskPrincipal `
        -Description "KEN personal assistant web app (FastAPI on 127.0.0.1:$Port). Reached from other devices via 'tailscale serve' at /ken." | Out-Null
    Write-Host "Registered $TaskName." -ForegroundColor Green
}

Write-Host "`nStarting it now..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 6

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "LastTaskResult: $($info.LastTaskResult)  (0 or 267009 = fine; 267009 means 'still running')"

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 10
    Write-Host "KEN is UP - model $($health.model), provider $($health.provider)." -ForegroundColor Green
} catch {
    Write-Host "KEN did not answer on :$Port yet. Check with:" -ForegroundColor Yellow
    Write-Host "  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
}

Write-Host "`nIt will now start automatically every time you log in."
Write-Host "Stop/disable:  Stop-ScheduledTask -TaskName $TaskName   /   Disable-ScheduledTask -TaskName $TaskName"
