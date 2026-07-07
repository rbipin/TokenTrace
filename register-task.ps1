# Registers ai-token-tracer as a Windows Scheduled Task.
# Runs "tokentracer collect --lookback 1" daily at 23:50 if the packaged
# command is on PATH (pipx / uv tool install); otherwise falls back to
# running tracker.py from this repo checkout with the python on PATH.
# Run as your normal user (no admin required). Safe to re-run: an existing
# task with the same name is fully replaced.
# To remove:  Unregister-ScheduledTask -TaskName "ai-token-tracer" -Confirm:$false

$taskName = "ai-token-tracer"

# -- Locate the command to run ----------------------------------------------
$tokentracer = Get-Command tokentracer -ErrorAction SilentlyContinue
if ($tokentracer) {
    # Packaged install: console script on PATH; db defaults to ~\.tokentracer\usage.db
    $exe      = $tokentracer.Source
    $argument = "collect --lookback 1"
    $workDir  = $HOME
    $dbHint   = "$HOME\.tokentracer\usage.db"
    Write-Host "Using packaged tokentracer: $exe"
} else {
    # Repo checkout: run tracker.py next to this script with python on PATH
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $python) {
        Write-Error "Neither 'tokentracer' nor 'python' found on PATH"
        exit 1
    }
    $scriptPath = Join-Path $PSScriptRoot "tracker.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Error "tracker.py not found at: $scriptPath"
        exit 1
    }
    $exe      = $python.Source
    $argument = "`"$scriptPath`" collect --lookback 1"
    $workDir  = $PSScriptRoot
    $dbHint   = "$PSScriptRoot\usage.db"
    Write-Host "Using repo checkout: $scriptPath (python: $exe)"
}

# -- Build task components -------------------------------------------------
$action = New-ScheduledTaskAction `
    -Execute $exe `
    -Argument $argument `
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
    -Description "Collects AI tool token usage (Copilot CLI, Claude Code CLI) daily into $dbHint" `
    -Force | Out-Null

Write-Host ""
Write-Host "✔ Task registered: $taskName"
Write-Host "  Runs daily at 23:50 | lookback 1 day | db -> $dbHint"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now  : Start-ScheduledTask -TaskName '$taskName'"
Write-Host "  Last result: (Get-ScheduledTaskInfo -TaskName '$taskName').LastTaskResult"
Write-Host "  Remove     : Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
