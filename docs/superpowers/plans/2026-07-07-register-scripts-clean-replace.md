# Register Scripts: Clean Replace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make re-running `register-task.ps1` fully replace an existing scheduled task via `Register-ScheduledTask -Force`, and align `register-task.sh` messaging.

**Architecture:** Pure script edits — remove the exists/update branch in the PowerShell script in favor of a single `-Force` registration; the shell script already clean-replaces and only needs a message tweak.

**Tech Stack:** PowerShell 5+ (ScheduledTasks module), bash + launchctl.

## Global Constraints

- No automated tests exist for these scripts; validation is syntax parse checks only.
- Do NOT commit — the user commits manually.
- Scripts remain runnable as normal user (no admin/sudo).

---

### Task 1: Simplify register-task.ps1 to always Register with -Force

**Files:**
- Modify: `register-task.ps1`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by other tasks (standalone script).

- [ ] **Step 1: Replace the register/update block**

In `register-task.ps1`, replace this block:

```powershell
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
        -Description "Collects AI tool token usage (Copilot CLI, Claude Code CLI) daily into $workDir\usage.db" | Out-Null
}
```

with:

```powershell
# -- Register (replaces any existing task with the same name) ---------------
Register-ScheduledTask `
    -TaskName    $taskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Description "Collects AI tool token usage (Copilot CLI, Claude Code CLI) daily into $workDir\usage.db" `
    -Force | Out-Null
```

- [ ] **Step 2: Update the header comment**

Replace:

```powershell
# Run once as your normal user (no admin required).
```

with:

```powershell
# Run as your normal user (no admin required). Safe to re-run: an existing
# task with the same name is fully replaced.
```

- [ ] **Step 3: Parse-check the script**

Run:

```powershell
$null = [scriptblock]::Create((Get-Content -Raw C:\Repo\me\TokenTrace\register-task.ps1)); Write-Host "parse OK"
```

Expected: `parse OK` (no parser exception).

### Task 2: Align register-task.sh messaging

**Files:**
- Modify: `register-task.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Update the unload message**

In `register-task.sh`, replace:

```bash
    echo "Job '$LABEL' already loaded — unloading first..."
```

with:

```bash
    echo "Job '$LABEL' already loaded — replacing existing job..."
```

- [ ] **Step 2: Syntax-check the script**

Run:

```powershell
bash -n C:\Repo\me\TokenTrace\register-task.sh; echo "exit=$LASTEXITCODE"
```

Expected: `exit=0`. (If bash is unavailable on this machine, skip — change is a string literal only.)
