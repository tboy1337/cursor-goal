# install-smoke.ps1 — Windows installer smoke test
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-smoke.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$tmpHome = Join-Path ([IO.Path]::GetTempPath()) ("cursor-goal-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmpHome | Out-Null

try {
    Write-Host "[install-smoke] HOME=$tmpHome"
    Write-Host "[install-smoke] Installing..."

    $env:CURSOR_GOAL_SKIP_MAIN = "1"
    . (Join-Path $repoRoot "scripts\install-goal.ps1")
    $code = Invoke-GoalInstall -HomeDir $tmpHome -RepoRoot $repoRoot
    if ($code -ne 0) {
        throw "Invoke-GoalInstall failed with exit $code"
    }

    $installDir = Join-Path $tmpHome ".cursor\skills\goal"
    $hooksFile = Join-Path $tmpHome ".cursor\hooks.json"
    $runGoal = Join-Path $installDir "scripts\run_goal.py"
    $stopCmd = Join-Path $installDir "scripts\stop_hook.cmd"

    if (-not (Test-Path (Join-Path $installDir "cursor_goal\__init__.py"))) {
        throw "Missing installed package"
    }
    if (-not (Test-Path $runGoal)) { throw "Missing run_goal.py" }
    if (-not (Test-Path $stopCmd)) { throw "Missing stop_hook.cmd" }
    if (-not (Test-Path $hooksFile)) { throw "Missing hooks.json" }
    $versionFile = Join-Path $installDir "VERSION"
    if (-not (Test-Path $versionFile)) { throw "Missing VERSION stamp" }
    $agentsDir = Join-Path $tmpHome ".cursor\agents"
    if (-not (Test-Path (Join-Path $agentsDir "goalKeeper.md"))) {
        throw "Missing goalKeeper.md"
    }
    if (-not (Test-Path (Join-Path $agentsDir "goal-evaluator.md"))) {
        throw "Missing goal-evaluator.md"
    }
    $keeper = Get-Content -LiteralPath (Join-Path $agentsDir "goalKeeper.md") -Raw
    if ($keeper -notmatch "goalKeeper|Autonomous|goal") {
        throw "goalKeeper.md content looks empty/wrong"
    }

    $hooks = Get-Content -LiteralPath $hooksFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $entry = @($hooks.hooks.stop)[0]
    if ($entry._cursor_goal -ne "cursor_goal_stop_hook") {
        throw "Hook marker missing: $($entry | ConvertTo-Json -Compress)"
    }
    if ($null -ne $entry.loop_limit) {
        throw "loop_limit should be null"
    }
    $cmd = [string]$entry.command
    if ($cmd -notmatch "stop_hook\.cmd") {
        throw "Hook command should reference stop_hook.cmd: $cmd"
    }
    Write-Host "[install-smoke] Hook command: $cmd"
    Write-Host ("[install-smoke] VERSION={0}" -f ((Get-Content -LiteralPath $versionFile -Raw).Trim()))

    $py = Find-GoalPython
    if (-not $py) { throw "Python 3.12+ required for smoke verify" }
    Write-Host "[install-smoke] Running manage status..."
    $statusOut = & $py.Exe @($py.PrefixArgs) "-u" $runGoal "manage" "status" 2>&1 | Out-String
    if ($statusOut -notmatch "No active goal") {
        throw "Unexpected manage status output: $statusOut"
    }

    Write-Host "[install-smoke] Running eval spawn-config..."
    $spawnOut = & $py.Exe @($py.PrefixArgs) "-u" $runGoal "eval" "spawn-config" 2>&1 | Out-String
    $spawn = $spawnOut.Trim()
    $spawnObj = $spawn | ConvertFrom-Json
    if ($spawnObj.subagent_type -ne "goal-evaluator") {
        throw "Unexpected spawn-config: $spawn"
    }
    if ($spawnObj.readonly -ne $true) {
        throw "spawn-config readonly should be true: $spawn"
    }
    if (-not $spawnObj.model) {
        throw "spawn-config missing model: $spawn"
    }
    Write-Host "[install-smoke] spawn-config: $spawn"

    Write-Host "[install-smoke] Uninstalling..."
    $env:CURSOR_GOAL_SKIP_MAIN = "1"
    . (Join-Path $repoRoot "scripts\uninstall-goal.ps1")
    $uCode = Invoke-GoalUninstall -HomeDir $tmpHome -PurgeData
    if ($uCode -ne 0) {
        throw "Invoke-GoalUninstall failed with exit $uCode"
    }
    if (Test-Path $installDir) {
        throw "Skill dir still present after uninstall"
    }

    Write-Host "[install-smoke] OK"
    exit 0
}
finally {
    if (Test-Path -LiteralPath $tmpHome) {
        Remove-Item -Recurse -Force -LiteralPath $tmpHome -ErrorAction SilentlyContinue
    }
}
