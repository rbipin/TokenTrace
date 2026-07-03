# Registers the ai-token-tracer as a Windows Scheduled Task.
# Run once as your normal user (no admin required).
# To remove:  Unregister-ScheduledTask -TaskName "ai-token-tracer" -Confirm:$false

$pythonExe  = "C:\Program Files\python\python312\python.exe"
$scriptPath = "C:\Repo\me\ai-token\tracker.py"
$workDir    = "C:\Repo\me\ai-token"
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

# -- Register (or update if already exists) --------------------------------
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$taskName' already exists — updating..."
    Set-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
} else {
    Register-ScheduledTask `
        -TaskName    $taskName `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -Description "Collects Copilot CLI/VSCode token usage hourly into $workDir\usage.db" | Out-Null
}

Write-Host ""
Write-Host "✔ Task registered: $taskName"
Write-Host "  Runs daily at 23:50 | lookback 1 day | db -> $workDir\usage.db"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now  : Start-ScheduledTask -TaskName '$taskName'"
Write-Host "  Last result: (Get-ScheduledTaskInfo -TaskName '$taskName').LastTaskResult"
Write-Host "  Remove     : Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
