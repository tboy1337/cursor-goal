# install-goal.ps1 — Install /goal Python harness for Cursor on Windows
#
# Usage (from a full clone or a GitHub source archive for a tagged release):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-goal.ps1
#
# Prefer this script on native Windows Cursor (writes stop_hook.cmd).
# Do not use install-goal.sh from Git Bash against Windows Cursor.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:HookMarker = "cursor_goal_stop_hook"

function Write-GoalInfo {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[install-goal] $Message" -ForegroundColor Green
}

function Write-GoalWarn {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[install-goal] $Message" -ForegroundColor Yellow
}

function Write-GoalErr {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[install-goal] $Message" -ForegroundColor Red
}

function Write-Utf8NoBomFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

function Find-GoalPython {
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()
    $candidates = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $versionArgs = $c.Args + @(
                "-c",
                "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable); raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
            )
            $out = @(& $c.File @versionArgs 2>$null)
            if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2) {
                $version = [string]$out[0]
                $absolute = [string]$out[-1]
                if (-not [string]::IsNullOrWhiteSpace($absolute)) {
                    return @{
                        Exe        = $absolute.Trim()
                        PrefixArgs = @()
                        Version    = $version.Trim()
                    }
                }
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function Get-GoalHookCommand {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][hashtable]$Python,
        [Parameter(Mandatory = $true)][string]$StopScript
    )
    # Prefer the .cmd launcher so Cursor's PowerShell bootstrap execs cmd.exe,
    # shrinking stdout capture races on Windows.
    $cmdPath = [IO.Path]::ChangeExtension($StopScript, '.cmd')
    if (Test-Path -LiteralPath $cmdPath) {
        if ($cmdPath -match '\s') {
            return '"' + $cmdPath + '"'
        }
        return $cmdPath
    }
    $parts = @($Python.Exe) + @($Python.PrefixArgs) + @("-u", $StopScript)
    return (($parts | ForEach-Object {
                if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
            }) -join " ")
}

function Write-GoalStopHookCmd {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][hashtable]$Python,
        [Parameter(Mandatory = $true)][string]$StopScriptPy,
        [Parameter(Mandatory = $true)][string]$StopScriptCmd
    )
    $pyExe = $Python.Exe
    $prefix = @($Python.PrefixArgs)
    $prefixPart = if ($prefix.Count -gt 0) {
        (($prefix | ForEach-Object {
                    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
                }) -join ' ') + ' '
    }
    else { '' }
    # cmd.exe batch: bake absolute interpreter + script paths.
    $content = @"
@echo off
setlocal
set PYTHONUNBUFFERED=1
"$pyExe" $prefixPart-u "$StopScriptPy"
exit /b %ERRORLEVEL%
"@
    Write-Utf8NoBomFile -Path $StopScriptCmd -Content $content
}

function Merge-GoalStopHook {
    <#
    .SYNOPSIS
    Legacy PowerShell hooks merge (kept for Pester unit tests).

    Install/uninstall production paths use Python cursor_goal.hooks_config via
    Invoke-GoalHooksConfigMerge so Windows and Unix stay in sync.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]$Data,
        [Parameter(Mandatory = $true)][string]$HookCommand,
        [Parameter(Mandatory = $true)][string]$Marker
    )
    $hasHooks = $null -ne ($Data.PSObject.Properties['hooks']) -and $null -ne $Data.hooks
    if (-not $hasHooks) {
        $Data | Add-Member -NotePropertyName hooks -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    $hasStop = $null -ne ($Data.hooks.PSObject.Properties['stop']) -and $null -ne $Data.hooks.stop
    if (-not $hasStop) {
        $Data.hooks | Add-Member -NotePropertyName stop -NotePropertyValue @() -Force
    }

    $hookEntry = [ordered]@{
        command      = $HookCommand
        loop_limit   = $null
        timeout      = 30
        _cursor_goal = $Marker
    }

    $filtered = @()
    foreach ($item in @($Data.hooks.stop)) {
        $cmd = ""
        if ($null -ne ($item.PSObject.Properties['command']) -and $null -ne $item.command) {
            $cmd = [string]$item.command
        }
        $keep = $true
        if ($cmd -match "goal-stop\.sh|stop_hook\.py|stop_hook\.cmd|cursor_goal stop|cursor-goal stop") {
            $keep = $false
        }
        $itemMarker = $null
        if ($null -ne ($item.PSObject.Properties['_cursor_goal'])) {
            $itemMarker = $item._cursor_goal
        }
        if ($itemMarker -eq $Marker) { $keep = $false }
        if ($keep) { $filtered += $item }
    }
    $filtered += [pscustomobject]$hookEntry
    $Data.hooks.stop = $filtered
    $hasVersion = $null -ne ($Data.PSObject.Properties['version']) -and $null -ne $Data.version
    if (-not $hasVersion) {
        $Data | Add-Member -NotePropertyName version -NotePropertyValue 1 -Force
    }
    return $Data
}

function Invoke-GoalHooksConfigMerge {
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)][hashtable]$Python,
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string]$HooksFile,
        [Parameter(Mandatory = $true)][string]$HookCommand
    )
    $tmpPy = Join-Path ([IO.Path]::GetTempPath()) ("cg-hooks-" + [guid]::NewGuid().ToString('N') + ".py")
    # Single-quoted here-string: no PowerShell expansion of Python source.
    $script = @'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from cursor_goal.hooks_config import merge_hooks_at_path
merge_hooks_at_path(Path(sys.argv[2]), sys.argv[3])
print("merged")
'@
    try {
        Write-Utf8NoBomFile -Path $tmpPy -Content $script
        $null = & $Python.Exe @($Python.PrefixArgs) $tmpPy $InstallDir $HooksFile $HookCommand 2>&1
        return [int]$LASTEXITCODE
    }
    finally {
        Remove-Item -Force $tmpPy -ErrorAction SilentlyContinue
    }
}

function Invoke-GoalInstall {
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [string]$HomeDir = $HOME,
        [string]$RepoRoot = "",
        [hashtable]$Python = $null
    )

    if (-not $RepoRoot) {
        $scriptDir = Split-Path -Parent $PSCommandPath
        if (-not $scriptDir) {
            $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
        }
        $RepoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
    }

    $installDir = Join-Path $HomeDir ".cursor\skills\goal"
    $agentsDir = Join-Path $HomeDir ".cursor\agents"
    $dataDir = Join-Path $HomeDir ".cursor-goal\data"
    $hooksFile = Join-Path $HomeDir ".cursor\hooks.json"

    Write-Host ""
    Write-Host "================================" -ForegroundColor Blue
    Write-Host " Installing /goal skill" -ForegroundColor Blue
    Write-Host " Python harness for Cursor" -ForegroundColor Blue
    Write-Host "================================" -ForegroundColor Blue
    Write-Host ""

    if (-not $Python) {
        $Python = Find-GoalPython
    }
    if (-not $Python) {
        Write-GoalErr "Python 3.12+ is required (py -3, python, or python3)."
        Write-GoalErr "Install from https://www.python.org/downloads/ then re-run."
        return 1
    }
    Write-GoalInfo ("Using interpreter: {0} ({1})" -f $Python.Exe, $Python.Version)

    $sourcePkg = Join-Path $RepoRoot "src\cursor_goal"
    $sourceSkill = Join-Path $RepoRoot ".cursor\skills\goal"
    $sourceAgent = Join-Path $RepoRoot ".cursor\agents\goalKeeper.md"
    $sourceEvaluator = Join-Path $RepoRoot ".cursor\agents\goal-evaluator.md"

    if (-not (Test-Path (Join-Path $sourcePkg "__init__.py"))) {
        Write-GoalErr "Package not found: $sourcePkg"
        Write-GoalErr "Run from a full cursor-goal clone or release tarball (not a lone download of this script)."
        return 1
    }
    if (-not (Test-Path (Join-Path $sourceSkill "SKILL.md"))) {
        Write-GoalErr "SKILL.md not found under $sourceSkill"
        return 1
    }
    if (-not (Test-Path $sourceAgent)) {
        Write-GoalErr "Required agent not found: $sourceAgent"
        return 1
    }
    if (-not (Test-Path $sourceEvaluator)) {
        Write-GoalErr "Required agent not found: $sourceEvaluator"
        return 1
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $installDir "scripts") | Out-Null
    New-Item -ItemType Directory -Force -Path $agentsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

    $targetPkg = Join-Path $installDir "cursor_goal"
    $skillBak = $null
    if (Test-Path (Join-Path $installDir "SKILL.md")) {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $skillBak = "$installDir.bak.$stamp"
        if (Test-Path $skillBak) { Remove-Item -Recurse -Force $skillBak }
        Copy-Item -Recurse $installDir $skillBak
        Write-GoalInfo "Backed up previous skill install to $skillBak"
    }
    if (Test-Path $targetPkg) { Remove-Item -Recurse -Force $targetPkg }
    Copy-Item -Recurse $sourcePkg $targetPkg
    Get-ChildItem -Path $targetPkg -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $sourceSkill "SKILL.md") (Join-Path $installDir "SKILL.md") -Force
    Copy-Item (Join-Path $sourceSkill "scripts\stop_hook.py") (Join-Path $installDir "scripts\stop_hook.py") -Force
    Copy-Item (Join-Path $sourceSkill "scripts\run_goal.py") (Join-Path $installDir "scripts\run_goal.py") -Force
    Copy-Item (Join-Path $sourceSkill "scripts\wake_loop.cmd") (Join-Path $installDir "scripts\wake_loop.cmd") -Force
    if (Test-Path (Join-Path $sourceSkill "scripts\wake_loop.sh")) {
        Copy-Item (Join-Path $sourceSkill "scripts\wake_loop.sh") (Join-Path $installDir "scripts\wake_loop.sh") -Force
    }

    $stopScript = Join-Path $installDir "scripts\stop_hook.py"
    $stopCmd = Join-Path $installDir "scripts\stop_hook.cmd"
    Write-GoalStopHookCmd -Python $Python -StopScriptPy $stopScript -StopScriptCmd $stopCmd
    $hookCommand = Get-GoalHookCommand -Python $Python -StopScript $stopScript

    $tmpVer = Join-Path ([IO.Path]::GetTempPath()) ("cg-ver-" + [guid]::NewGuid().ToString('N') + ".py")
    $versionScript = @'
import sys
from pathlib import Path
install = Path(sys.argv[1])
sys.path.insert(0, str(install))
from cursor_goal import __version__
(install / "VERSION").write_text(__version__ + "\n", encoding="utf-8")
print(__version__)
'@
    try {
        Write-Utf8NoBomFile -Path $tmpVer -Content $versionScript
        $null = & $Python.Exe @($Python.PrefixArgs) $tmpVer $installDir 2>&1
    }
    finally {
        Remove-Item -Force $tmpVer -ErrorAction SilentlyContinue
    }

    @(
        "goal-manage.sh", "goal-stop.sh", "goal-eval.sh", "goal-parse.sh"
    ) | ForEach-Object {
        $legacy = Join-Path $installDir $_
        if (Test-Path $legacy) { Remove-Item -Force $legacy }
    }

    Copy-Item $sourceAgent (Join-Path $agentsDir "goalKeeper.md") -Force
    Write-GoalInfo "Installed: $(Join-Path $agentsDir 'goalKeeper.md')"
    Copy-Item $sourceEvaluator (Join-Path $agentsDir "goal-evaluator.md") -Force
    Write-GoalInfo "Installed: $(Join-Path $agentsDir 'goal-evaluator.md')"

    New-Item -ItemType Directory -Force -Path (Join-Path $HomeDir ".cursor") | Out-Null

    $hooksBak = $null
    if (Test-Path $hooksFile) {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $hooksBak = "$hooksFile.bak.$stamp"
        Copy-Item $hooksFile $hooksBak -Force
        Write-GoalInfo "Backed up existing hooks.json to $hooksBak"
    }

    $mergeCode = Invoke-GoalHooksConfigMerge -Python $Python -InstallDir $installDir -HooksFile $hooksFile -HookCommand $hookCommand
    if ($mergeCode -ne 0) {
        Write-GoalErr "Failed to merge stop hook via hooks_config (exit $mergeCode)."
        if ($hooksBak -and (Test-Path -LiteralPath $hooksBak)) {
            Write-GoalWarn "Restoring hooks.json from $hooksBak"
            Copy-Item -LiteralPath $hooksBak -Destination $hooksFile -Force
            Write-GoalInfo "Restored previous hooks.json."
        }
        if ($skillBak -and (Test-Path -LiteralPath $skillBak)) {
            Write-GoalWarn "Restoring skill files from $skillBak"
            if (Test-Path -LiteralPath $installDir) {
                Remove-Item -Recurse -Force -LiteralPath $installDir
            }
            Move-Item -LiteralPath $skillBak -Destination $installDir
            Write-GoalInfo "Restored previous skill install."
        }
        else {
            Write-GoalErr "Skill files were installed under $installDir (no prior backup to restore)."
        }
        return 1
    }
    Write-GoalInfo "Merged/upgraded stop hook in hooks.json"

    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host " /goal Autonomous Loop - Installed!" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Stop hook command: $hookCommand"
    Write-Host "Verify:"
    Write-Host ("  {0} -u {1} manage status" -f $Python.Exe, (Join-Path $installDir "scripts\run_goal.py"))
    Write-GoalInfo "Windows stop hook uses stop_hook.cmd + stdout drain delay (Cursor capture race mitigation)."
    Write-GoalInfo "Wake watchdog: after create/resume, start wake loop with notify_on_output on ^AGENT_GOAL_WAKE."
    Write-GoalWarn "If stop followups still drop, wake continues the goal; last-stop-response.json is always written."
    Write-GoalWarn "Re-run the installer after moving/upgrading Python (stop_hook.cmd bakes an absolute interpreter path)."
    Write-Host ""
    return 0
}

# Direct-invocation guard: skip when dot-sourced by Pester/tests.
$script:IsDotSourced = $MyInvocation.InvocationName -eq '.' -or $env:CURSOR_GOAL_SKIP_MAIN -eq '1'
if (-not $script:IsDotSourced) {
    exit (Invoke-GoalInstall)
}
