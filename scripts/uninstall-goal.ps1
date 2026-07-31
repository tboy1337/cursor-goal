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
            $cmd = ""
            if ($null -ne ($item.PSObject.Properties['command']) -and $null -ne $item.command) {
                $cmd = [string]$item.command
            }
            $drop = $false
            $itemMarker = $null
            if ($null -ne ($item.PSObject.Properties['_cursor_goal'])) {
                $itemMarker = $item._cursor_goal
            }
            if ($itemMarker -eq $Marker) { $drop = $true }
            if ($cmd -match "goal-stop\.sh|stop_hook\.py|stop_hook\.cmd|cursor_goal stop|cursor-goal stop") {
                $drop = $true
            }
            if (-not $drop) { $filtered += $item }
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
            $data = Get-Content -Raw -Path $hooksFile | ConvertFrom-Json
            $data = Select-GoalStopHooksRemaining -Data $data -Marker $script:HookMarker
            Write-UninstallUtf8NoBom -Path $hooksFile -Content (($data | ConvertTo-Json -Depth 10) + "`n")
        }
        Write-Host "[uninstall-goal] Removed stop hook entries from hooks.json"
    }

    Write-Host "[uninstall-goal] Removing skill at $installDir"
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }

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
