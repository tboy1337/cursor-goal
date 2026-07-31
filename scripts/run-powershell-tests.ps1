# Run PowerShell script analyzer + Pester with >=95% code coverage.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-powershell-tests.ps1

[CmdletBinding()]
param(
    [double]$CoverageThreshold = 95.0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Scripts = @(
    (Join-Path $RepoRoot "scripts\install-goal.ps1"),
    (Join-Path $RepoRoot "scripts\uninstall-goal.ps1")
)
$TestPath = Join-Path $RepoRoot "tests\powershell"

Write-Host "[ps-tests] Running PSScriptAnalyzer..."
$analyzerModule = Get-Module -ListAvailable PSScriptAnalyzer | Sort-Object Version -Descending | Select-Object -First 1
if (-not $analyzerModule) {
    Write-Error "PSScriptAnalyzer is required. Install with: Install-Module PSScriptAnalyzer -Scope CurrentUser"
    exit 2
}
Import-Module PSScriptAnalyzer -Force

$settingsPath = Join-Path $RepoRoot "PSScriptAnalyzerSettings.psd1"
$issues = @()
foreach ($script in $Scripts) {
    $issues += @(
        Invoke-ScriptAnalyzer -Path $script -Settings $settingsPath -Severity @("Error", "Warning", "Information")
    )
}
if ($issues.Count -gt 0) {
    $issues | Format-Table -AutoSize RuleName, Severity, ScriptName, Line, Message
    Write-Error ("PSScriptAnalyzer found {0} issue(s)" -f $issues.Count)
    exit 1
}
Write-Host "[ps-tests] PSScriptAnalyzer: clean"

$pesterModule = Get-Module -ListAvailable Pester | Where-Object { $_.Version -ge [version]"5.0.0" } | Sort-Object Version -Descending | Select-Object -First 1
if (-not $pesterModule) {
    Write-Error "Pester 5+ is required. Install with: Install-Module Pester -MinimumVersion 5.0.0 -Scope CurrentUser -Force"
    exit 2
}
Import-Module Pester -MinimumVersion 5.0.0 -Force

Write-Host "[ps-tests] Running Pester with code coverage (threshold=$CoverageThreshold%)..."
$config = New-PesterConfiguration
$config.Run.Path = $TestPath
$config.Run.Exit = $false
$config.Run.PassThru = $true
$config.Output.Verbosity = "Detailed"
$config.CodeCoverage.Enabled = $true
$config.CodeCoverage.Path = $Scripts
# Disable Pester's built-in fail-under so we can report a clear message ourselves.
$config.CodeCoverage.CoveragePercentTarget = 0
$config.TestResult.Enabled = $false

$result = Invoke-Pester -Configuration $config

$failedCount = 0
if ($null -ne $result) {
    $failedProp = $result.PSObject.Properties | Where-Object { $_.Name -eq 'FailedCount' }
    if ($null -ne $failedProp) {
        $failedCount = [int]$failedProp.Value
    }
}
if ($failedCount -gt 0) {
    Write-Error ("Pester failed: {0} failed test(s)" -f $failedCount)
    exit 1
}

$coverage = $null
if ($null -ne $result) {
    $covProp = $result.PSObject.Properties | Where-Object { $_.Name -eq 'CodeCoverage' }
    if ($null -ne $covProp) {
        $coverage = $covProp.Value
    }
}
if ($null -eq $coverage) {
    Write-Error "Pester did not produce code coverage results"
    exit 1
}

# Pester 5 exposes coverage percent on CoveragePercent / NumberOfCommands*
$percent = $null
$pctProp = $coverage.PSObject.Properties | Where-Object { $_.Name -eq 'CoveragePercent' }
if ($null -ne $pctProp -and $null -ne $pctProp.Value) {
    $percent = [double]$pctProp.Value
}
else {
    $analyzedProp = $coverage.PSObject.Properties | Where-Object { $_.Name -eq 'NumberOfCommandsAnalyzed' }
    $executedProp = $coverage.PSObject.Properties | Where-Object { $_.Name -eq 'NumberOfCommandsExecuted' }
    if ($null -ne $analyzedProp -and [int]$analyzedProp.Value -gt 0 -and $null -ne $executedProp) {
        $percent = (100.0 * [double]$executedProp.Value / [double]$analyzedProp.Value)
    }
}

if ($null -eq $percent) {
    Write-Error "Unable to determine PowerShell coverage percent"
    exit 1
}

Write-Host ("[ps-tests] PowerShell command coverage: {0:N2}%" -f $percent)
if ($percent -lt $CoverageThreshold) {
    Write-Error ("PowerShell coverage {0:N2}% is below {1}%" -f $percent, $CoverageThreshold)
    exit 1
}

Write-Host "[ps-tests] All PowerShell checks passed"
exit 0
