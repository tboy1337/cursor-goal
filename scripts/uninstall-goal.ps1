# uninstall-goal.ps1 — Remove /goal install artifacts from Cursor user config
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-goal.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-goal.ps1 -PurgeData

[CmdletBinding()]
param(
    [switch]$PurgeData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:HookMarker = "cursor_goal_stop_hook"

function Write-UninstallUtf8NoBom {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

function Select-GoalStopHooksRemaining {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]$Data,
        [Parameter(Mandatory = $true)][string]$Marker
    )
    $hasHooks = $null -ne ($Data.PSObject.Properties['hooks']) -and $null -ne $Data.hooks
    $hasStop = $hasHooks -and ($null -ne ($Data.hooks.PSObject.Properties['stop'])) -and $null -ne $Data.hooks.stop
    if ($hasStop) {
        $filtered = @()
        foreach ($item in @($Data.hooks.stop)) {
            $itemMarker = $null
            if ($null -ne ($item.PSObject.Properties['_cursor_goal'])) {
                $itemMarker = $item._cursor_goal
            }
            if ($itemMarker -ne $Marker) {
                $filtered += $item
            }
        }
        $Data.hooks.stop = $filtered
    }
    return $Data
}

function Invoke-GoalUninstall {
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [string]$HomeDir = $HOME,
        [switch]$PurgeData
    )

    $installDir = Join-Path $HomeDir ".cursor\skills\goal"
    $agentsDir = Join-Path $HomeDir ".cursor\agents"
    $hooksFile = Join-Path $HomeDir ".cursor\hooks.json"

    if (Test-Path $hooksFile) {
        $pkgDir = Join-Path $installDir "cursor_goal"
        $cleaned = $false
        if (Test-Path $pkgDir) {
            $py = $null
            $pyArgs = @()
            if (Get-Command py -ErrorAction SilentlyContinue) {
                $py = "py"
                $pyArgs = @("-3")
            }
            elseif (Get-Command python -ErrorAction SilentlyContinue) {
                $py = "python"
                $pyArgs = @()
            }
            if ($py) {
                $tmpPy = Join-Path ([IO.Path]::GetTempPath()) ("cg-unhooks-" + [guid]::NewGuid().ToString('N') + ".py")
                $script = @'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from cursor_goal.hooks_config import remove_hooks_at_path
remove_hooks_at_path(Path(sys.argv[2]))
print("hooks cleaned")
'@
                try {
                    Write-UninstallUtf8NoBom -Path $tmpPy -Content $script
                    $null = & $py @($pyArgs + @($tmpPy, $installDir, $hooksFile)) 2>&1
                    if ($LASTEXITCODE -eq 0) { $cleaned = $true }
                }
                finally {
                    Remove-Item -Force $tmpPy -ErrorAction SilentlyContinue
                }
            }
        }
        if (-not $cleaned) {
            # Package unavailable — still prefer Python JSON rewrite over ConvertTo-Json.
            $py = $null
            $pyArgs = @()
            if (Get-Command py -ErrorAction SilentlyContinue) {
                $py = "py"
                $pyArgs = @("-3")
            }
            elseif (Get-Command python -ErrorAction SilentlyContinue) {
                $py = "python"
                $pyArgs = @()
            }
            if ($py) {
                $tmpPy = Join-Path ([IO.Path]::GetTempPath()) ("cg-unhooks2-" + [guid]::NewGuid().ToString('N') + ".py")
                $script = @'
import json
import os
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
hooks = data.get("hooks") or {}
stop = hooks.get("stop") or []

def is_goal_hook(item):
    if not isinstance(item, dict):
        return False
    return item.get("_cursor_goal") == "cursor_goal_stop_hook"

hooks["stop"] = [
    item for item in stop if not is_goal_hook(item)
]
data["hooks"] = hooks
tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(path)
print("hooks cleaned")
'@
                try {
                    Write-UninstallUtf8NoBom -Path $tmpPy -Content $script
                    $null = & $py @($pyArgs + @($tmpPy, $hooksFile)) 2>&1
                    if ($LASTEXITCODE -eq 0) { $cleaned = $true }
                }
                finally {
                    Remove-Item -Force $tmpPy -ErrorAction SilentlyContinue
                }
            }
        }
        if (-not $cleaned) {
            Write-Host "[uninstall-goal] ACTION REQUIRED: could not clean stop hooks from:"
            Write-Host "  $hooksFile"
            Write-Host "  Leaving skill tree in place so hooks do not point at deleted files."
            Write-Host "  Remove cursor-goal stop hook entries manually, then re-run uninstall."
            return 1
        }
        else {
            Write-Host "[uninstall-goal] Removed stop hook entries from hooks.json"
        }
    }

    # Best-effort wake disarm before deleting the skill tree.
    $runGoal = Join-Path $installDir "scripts\run_goal.py"
    if (Test-Path $runGoal) {
        $py = $null
        $pyArgs = @()
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $py = "py"
            $pyArgs = @("-3")
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $py = "python"
            $pyArgs = @()
        }
        if ($py) {
            try {
                $null = & $py @($pyArgs + @("-u", $runGoal, "wake", "disarm")) 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[uninstall-goal] Disarmed wake watchdog"
                }
                else {
                    Write-Host "[uninstall-goal] Warning: wake disarm failed (continuing uninstall)"
                }
            }
            catch {
                Write-Host "[uninstall-goal] Warning: wake disarm failed (continuing uninstall)"
            }
        }
    }

    Write-Host "[uninstall-goal] Removing skill at $installDir"
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }

    # Clean installer backup debris next to the skill / agents trees.
    $skillsParent = Join-Path $HomeDir ".cursor\skills"
    if (Test-Path -LiteralPath $skillsParent) {
        Get-ChildItem -LiteralPath $skillsParent -Directory -Filter "goal.bak.*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -Recurse -Force -LiteralPath $_.FullName -ErrorAction SilentlyContinue
                Write-Host ("[uninstall-goal] Removed backup {0}" -f $_.FullName)
            }
    }
    if (Test-Path -LiteralPath $agentsDir) {
        Get-ChildItem -LiteralPath $agentsDir -File -Filter "goalKeeper.md.bak.*" -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item -Force -LiteralPath $_.FullName -ErrorAction SilentlyContinue }
        Get-ChildItem -LiteralPath $agentsDir -File -Filter "goal-evaluator.md.bak.*" -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item -Force -LiteralPath $_.FullName -ErrorAction SilentlyContinue }
    }

    $agent = Join-Path $agentsDir "goalKeeper.md"
    if (Test-Path $agent) {
        Remove-Item -Force $agent
        Write-Host "[uninstall-goal] Removed $agent"
    }
    $evaluator = Join-Path $agentsDir "goal-evaluator.md"
    if (Test-Path $evaluator) {
        Remove-Item -Force $evaluator
        Write-Host "[uninstall-goal] Removed $evaluator"
    }

    if ($PurgeData) {
        $goalRoot = Join-Path $HomeDir ".cursor-goal"
        if (Test-Path $goalRoot) {
            Remove-Item -Recurse -Force $goalRoot
            Write-Host "[uninstall-goal] Purged $goalRoot"
        }
    }
    else {
        Write-Host "[uninstall-goal] Left data under ~/.cursor-goal (pass -PurgeData to remove)"
    }

    Write-Host "[uninstall-goal] Done."
    return 0
}

# Direct-invocation guard: skip when dot-sourced by Pester/tests.
$script:IsDotSourced = $MyInvocation.InvocationName -eq '.' -or $env:CURSOR_GOAL_SKIP_MAIN -eq '1'
if (-not $script:IsDotSourced) {
    exit (Invoke-GoalUninstall -PurgeData:$PurgeData)
}
