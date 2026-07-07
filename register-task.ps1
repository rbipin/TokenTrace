# Registers ai-token-tracer as a Windows Scheduled Task.
# Runs tracker.py collect --lookback 1 daily at 23:50.
# Run as your normal user (no admin required). Safe to re-run: an existing
# task with the same name is fully replaced.
# To remove:  Unregister-ScheduledTask -TaskName "ai-token-tracer" -Confirm:$false

$pythonExe  = "C:\Program Files\python\python312\python.exe"
$scriptPath = "C:\Repo\me\TokenTrace\tracker.py"
$workDir    = "C:\Repo\me\TokenTrace"
$taskName   = "ai-token-tracer"

# -- Validate paths before registering -------------------------------------
if (-not (Test-Path $pythonExe)) {
    Write-Error "Python not found at: $pythonExe"
    exit 1
}
if (-not (Test-Path $scriptPath)) {
    Write-Error "tracker.py not found at: $scriptPath"
    exit 1
}

# -- Build task components -------------------------------------------------
$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "`"$scriptPath`" collect --lookback 1" `
    -WorkingDirectory $workDir

# Run daily at 11:50 PM
$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "23:50"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

# -- Register (replaces any existing task with the same name) ---------------
Register-ScheduledTask `
    -TaskName    $taskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Description "Collects AI tool token usage (Copilot CLI, Claude Code CLI) daily into $workDir\usage.db" `
    -Force | Out-Null

Write-Host ""
Write-Host "✔ Task registered: $taskName"
Write-Host "  Runs daily at 23:50 | lookback 1 day | db -> $workDir\usage.db"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now  : Start-ScheduledTask -TaskName '$taskName'"
Write-Host "  Last result: (Get-ScheduledTaskInfo -TaskName '$taskName').LastTaskResult"
Write-Host "  Remove     : Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
