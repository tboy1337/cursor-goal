# install-smoke.ps1 — Windows installer smoke test
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-smoke.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$tmpHome = Join-Path ([IO.Path]::GetTempPath()) ("cursor-goal-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmpHome | Out-Null

try {
    # Isolate Python expanduser("~") and goal data from the real profile.
    # Unix install-smoke.sh exports HOME and USERPROFILE for the same reason.
    $env:HOME = $tmpHome
    $env:USERPROFILE = $tmpHome
    Remove-Item Env:CURSOR_GOAL_DATA -ErrorAction SilentlyContinue
    Remove-Item Env:CURSOR_GOAL_HOME -ErrorAction SilentlyContinue
    Remove-Item Env:CURSOR_PLUGIN_ROOT -ErrorAction SilentlyContinue

    Write-Host "[install-smoke] HOME=$tmpHome"
    Write-Host "[install-smoke] Installing..."

    $env:CURSOR_GOAL_SKIP_MAIN = "1"
    . (Join-Path $repoRoot "scripts\install-goal.ps1")
    $code = Invoke-GoalInstall -HomeDir $tmpHome -RepoRoot $repoRoot
    if ($code -ne 0) {
        throw "Invoke-GoalInstall failed with exit $code"
    }

    $installDir = Join-Path $tmpHome ".cursor\skills\cursor-goal"
    $hooksFile = Join-Path $tmpHome ".cursor\hooks.json"
    $runGoal = Join-Path $installDir "scripts\run_goal.py"
    $stopCmd = Join-Path $installDir "scripts\stop_hook.cmd"

    if (-not (Test-Path (Join-Path $installDir "cursor_goal\__init__.py"))) {
        throw "Missing installed package"
    }
    if (-not (Test-Path $runGoal)) { throw "Missing run_goal.py" }
    if (-not (Test-Path $stopCmd)) { throw "Missing stop_hook.cmd" }
    $wakeCmd = Join-Path $installDir "scripts\wake_loop.cmd"
    if (-not (Test-Path $wakeCmd)) { throw "Missing wake_loop.cmd" }
    $wakeBody = Get-Content -Raw -LiteralPath $wakeCmd
    if ($wakeBody -notmatch 'wake loop') {
        throw "wake_loop.cmd should invoke wake loop"
    }
    if ($wakeBody -notmatch 'PYTHONUNBUFFERED=1') {
        throw "wake_loop.cmd should set PYTHONUNBUFFERED"
    }
    # Classic install bakes absolute Python (not PATH discovery).
    if ($wakeBody -match 'where py') {
        throw "Installed wake_loop.cmd should bake absolute Python (not PATH where)"
    }
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
    if (-not (Test-Path (Join-Path $agentsDir "goal-auditor.md"))) {
        throw "Missing goal-auditor.md"
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
    $subStop = @($hooks.hooks.subagentStop)
    if ($subStop.Count -ne 2) {
        throw "Expected 2 subagentStop entries, got $($subStop.Count)"
    }
    $subPairs = @{}
    foreach ($item in $subStop) {
        $subPairs[[string]$item._cursor_goal] = [string]$item.matcher
    }
    if ($subPairs["cursor_goal_subagent_stop_hook"] -ne "goal-evaluator") {
        throw "cursor_goal_subagent_stop_hook must pair with matcher goal-evaluator"
    }
    if ($subPairs["cursor_goal_subagent_audit_stop_hook"] -ne "goal-auditor") {
        throw "cursor_goal_subagent_audit_stop_hook must pair with matcher goal-auditor"
    }
    Write-Host "[install-smoke] subagentStop markers/matchers OK"
    Write-Host ("[install-smoke] VERSION={0}" -f ((Get-Content -LiteralPath $versionFile -Raw).Trim()))

    $py = Find-GoalPython
    if (-not $py) { throw "Python 3.12+ required for smoke verify" }
    Write-Host "[install-smoke] Running manage status..."
    $statusOut = & $py.Exe @($py.PrefixArgs) "-u" $runGoal "manage" "status" 2>&1 | Out-String
    if ($statusOut -notmatch "No active goal") {
        throw "Unexpected manage status output: $statusOut"
    }

    Write-Host "[install-smoke] Running manage doctor..."
    & $py.Exe @($py.PrefixArgs) "-u" $runGoal "manage" "doctor" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "manage doctor failed with exit $LASTEXITCODE"
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

    Write-Host "[install-smoke] Running eval audit-spawn-config..."
    $auditSpawnOut = & $py.Exe @($py.PrefixArgs) "-u" $runGoal "eval" "audit-spawn-config" 2>&1 | Out-String
    $auditSpawn = $auditSpawnOut.Trim()
    $auditObj = $auditSpawn | ConvertFrom-Json
    if ($auditObj.subagent_type -ne "goal-auditor") {
        throw "Unexpected audit-spawn-config: $auditSpawn"
    }
    if ($auditObj.readonly -ne $true) {
        throw "audit-spawn-config readonly should be true: $auditSpawn"
    }
    if ($auditObj.model -ne "inherit") {
        throw "audit-spawn-config model should be inherit: $auditSpawn"
    }
    Write-Host "[install-smoke] audit-spawn-config: $auditSpawn"

    Write-Host "[install-smoke] Checking v5 layout (no leftover user /goal skill)..."
    $legacySkill = Join-Path $tmpHome ".cursor\skills\goal"
    if (Test-Path $legacySkill) {
        throw "Leftover user skill $legacySkill still present"
    }
    $skillBaks = @(Get-ChildItem -Path (Join-Path $tmpHome ".cursor\skills") -Directory -Filter "goal.bak.*" -ErrorAction SilentlyContinue)
    if ($skillBaks.Count -gt 0) {
        throw ("Leftover goal.bak.* under skills: {0}" -f (($skillBaks | ForEach-Object { $_.Name }) -join ', '))
    }
    $backupRoot = Join-Path $tmpHome ".cursor-goal\backups"
    if (-not (Test-Path $backupRoot)) {
        throw "Missing backup root $backupRoot"
    }

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
    if (Test-Path $legacySkill) {
        throw "Legacy skill dir still present after uninstall"
    }

    Write-Host "[install-smoke] OK"
    exit 0
}
finally {
    if (Test-Path -LiteralPath $tmpHome) {
        Remove-Item -Recurse -Force -LiteralPath $tmpHome -ErrorAction SilentlyContinue
    }
}
