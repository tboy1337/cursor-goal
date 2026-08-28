# Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

BeforeAll {
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:InstallScript = Join-Path $RepoRoot 'scripts\install-goal.ps1'
    $script:UninstallScript = Join-Path $RepoRoot 'scripts\uninstall-goal.ps1'
    $env:CURSOR_GOAL_SKIP_MAIN = '1'
    . $InstallScript
    . $UninstallScript
}

AfterAll {
    Remove-Item Env:CURSOR_GOAL_SKIP_MAIN -ErrorAction SilentlyContinue
}

Describe 'Get-GoalPsLiteral' {
    It 'wraps values as PowerShell single-quoted literals' {
        Get-GoalPsLiteral -Value 'C:\Python\python.exe' | Should -Be "'C:\Python\python.exe'"
    }

    It 'escapes embedded single quotes' {
        Get-GoalPsLiteral -Value "C:\Users\O'Brien" | Should -Be "'C:\Users\O''Brien'"
    }
}

Describe 'Find-GoalPython' {
    It 'returns a Python 3.12+ interpreter on this machine' {
        $py = Find-GoalPython
        $py | Should -Not -BeNullOrEmpty
        $py.Exe | Should -Match '\.exe$|python'
        Test-Path -LiteralPath $py.Exe | Should -BeTrue
        $py.Version | Should -Match '^\d+\.\d+$'
        $py.PrefixArgs.Count | Should -Be 0
    }
}

Describe 'Get-GoalHookCommand' {
    It 'quotes paths that contain spaces' {
        $python = @{ Exe = 'py'; PrefixArgs = @('-3') }
        $cmd = Get-GoalHookCommand -Python $python -StopScript 'C:\Program Files\stop_hook.py'
        $cmd | Should -Match '"C:\\Program Files\\stop_hook.py"'
        $cmd | Should -Match '-u'
    }

    It 'leaves simple paths unquoted' {
        $python = @{ Exe = 'python'; PrefixArgs = @() }
        $cmd = Get-GoalHookCommand -Python $python -StopScript 'C:\stop_hook.py'
        $cmd | Should -Be 'python -u C:\stop_hook.py'
    }

    It 'prefers sibling stop_hook.cmd when present' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-cmd-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $py = Join-Path $dir 'stop_hook.py'
            $cmdFile = Join-Path $dir 'stop_hook.cmd'
            Set-Content -LiteralPath $py -Value '# py'
            Set-Content -LiteralPath $cmdFile -Value '@echo off'
            $python = @{ Exe = 'python'; PrefixArgs = @() }
            $cmd = Get-GoalHookCommand -Python $python -StopScript $py
            $cmd | Should -Be $cmdFile
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }

    It 'quotes sibling stop_hook.cmd when path has spaces' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg cmd " + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $py = Join-Path $dir 'stop_hook.py'
            $cmdFile = Join-Path $dir 'stop_hook.cmd'
            Set-Content -LiteralPath $py -Value '# py'
            Set-Content -LiteralPath $cmdFile -Value '@echo off'
            $python = @{ Exe = 'python'; PrefixArgs = @() }
            $cmd = Get-GoalHookCommand -Python $python -StopScript $py
            $cmd | Should -Be ('"' + $cmdFile + '"')
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Write-GoalStopHookCmd' {
    It 'writes unbuffered python -u launcher with empty prefix args' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-wcmd-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $py = Join-Path $dir 'stop_hook.py'
            $cmdFile = Join-Path $dir 'stop_hook.cmd'
            $python = @{ Exe = 'C:\Python\python.exe'; PrefixArgs = @() }
            Write-GoalStopHookCmd -Python $python -StopScriptPy $py -StopScriptCmd $cmdFile
            $body = Get-Content -Raw -LiteralPath $cmdFile
            $body | Should -Match 'PYTHONUNBUFFERED=1'
            $body | Should -Match '-u'
            $body.Contains($py) | Should -BeTrue
            $body | Should -Not -Match ' -3 '
            $body | Should -Match 'goto :use_cgp'
            $body | Should -Match '%CGP%'
            $body | Should -Match 'must be an absolute path'
            $body | Should -Match 'is not Python 3\.12\+'
            $body | Should -Match 'findstr /R'
            $body | Should -Match 'unsafe cmd metacharacters'
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }

    It 'quotes prefix args that contain spaces' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-wcmd2-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $py = Join-Path $dir 'stop_hook.py'
            $cmdFile = Join-Path $dir 'stop_hook.cmd'
            $python = @{ Exe = 'py.exe'; PrefixArgs = @('-3', 'with space') }
            Write-GoalStopHookCmd -Python $python -StopScriptPy $py -StopScriptCmd $cmdFile
            $body = Get-Content -Raw -LiteralPath $cmdFile
            $body | Should -Match '-3'
            $body | Should -Match '"with space"'
            $body | Should -Match 'PYTHONUNBUFFERED=1'
            $body.Contains($py) | Should -BeTrue
            $body | Should -Match '"%CGP%" -u'
            $body | Should -Match 'findstr /R'
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Write-GoalWakeLoopCmd' {
    It 'bakes absolute python and wake loop invocation' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-wwake-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $runGoal = Join-Path $dir 'run_goal.py'
            $cmdFile = Join-Path $dir 'wake_loop.cmd'
            $python = @{ Exe = 'C:\Python\python.exe'; PrefixArgs = @() }
            Write-GoalWakeLoopCmd -Python $python -RunGoalPy $runGoal -WakeLoopCmd $cmdFile
            $body = Get-Content -Raw -LiteralPath $cmdFile
            $body | Should -Match 'PYTHONUNBUFFERED=1'
            $body | Should -Match 'wake loop'
            $body.Contains($runGoal) | Should -BeTrue
            $body | Should -Not -Match 'where py'
            $body | Should -Match 'goto :use_cgp'
            $body | Should -Match '%CGP%'
            $body | Should -Match 'must be an absolute path'
            $body | Should -Match 'findstr /R'
            $body | Should -Match 'unsafe cmd metacharacters'
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }

    It 'quotes prefix args that contain spaces' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-wwake-sp-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $runGoal = Join-Path $dir 'run_goal.py'
            $cmdFile = Join-Path $dir 'wake_loop.cmd'
            $python = @{ Exe = 'py.exe'; PrefixArgs = @('-3', 'with space') }
            Write-GoalWakeLoopCmd -Python $python -RunGoalPy $runGoal -WakeLoopCmd $cmdFile
            $body = Get-Content -Raw -LiteralPath $cmdFile
            $body | Should -Match '-3'
            $body | Should -Match '"with space"'
            $body | Should -Match 'wake loop'
            $body | Should -Match '"%CGP%" -u'
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Protect-GoalDataDirAcl' {
    It 'hardens an existing data dir via package root' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-acl-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $tmpDir = Join-Path $dir 'tmp'
            $dataDir = Join-Path $dir 'data'
            New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            $python = Find-GoalPython
            $code = Protect-GoalDataDirAcl -Python $python -PackageRoot (Join-Path $RepoRoot 'src') -TmpDir $tmpDir -DataDir $dataDir
            $code | Should -Be 0
            Test-Path $tmpDir | Should -BeTrue
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }

    It 'returns 1 when python harden exits non-zero' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-acl-fail-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $tmpDir = Join-Path $dir 'tmp'
            $dataDir = Join-Path $dir 'data'
            New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            $python = @{ Exe = 'cmd.exe'; PrefixArgs = @('/c', 'exit', '7') }
            $code = Protect-GoalDataDirAcl -Python $python -PackageRoot (Join-Path $RepoRoot 'src') -TmpDir $tmpDir -DataDir $dataDir
            $code | Should -Be 1
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }

    It 'returns 1 when harden_windows_acl soft-fails without raising' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("cg-acl-soft-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        try {
            $pkgRoot = Join-Path $dir 'pkg'
            $tmpDir = Join-Path $dir 'tmp'
            $dataDir = Join-Path $dir 'data'
            New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            $pkg = Join-Path $pkgRoot 'cursor_goal'
            New-Item -ItemType Directory -Force -Path $pkg | Out-Null
            Set-Content -Path (Join-Path $pkg '__init__.py') -Value '' -Encoding utf8
            @'
def harden_windows_acl(path, *, force=False):
    return False

def failure_reason(path):
    return "simulated soft failure"
'@ | Set-Content -Path (Join-Path $pkg 'win_acl.py') -Encoding utf8
            $python = Find-GoalPython
            $code = Protect-GoalDataDirAcl -Python $python -PackageRoot $pkgRoot -TmpDir $tmpDir -DataDir $dataDir
            $code | Should -Be 1
        }
        finally {
            Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Merge-GoalStopHook' {
    It 'replaces marked goal hooks and keeps unmarked entries' {
        $data = [pscustomobject]@{
            version = 1
            hooks   = [pscustomobject]@{
                stop = @(
                    [pscustomobject]@{ command = '~/.cursor/skills/goal/goal-stop.sh' },
                    [pscustomobject]@{ command = './other.sh' },
                    [pscustomobject]@{ command = 'x'; _cursor_goal = 'cursor_goal_stop_hook' }
                )
            }
        }
        $merged = Merge-GoalStopHook -Data $data -HookCommand 'py -3 -u stop.py' -Marker 'cursor_goal_stop_hook'
        $stop = @($merged.hooks.stop)
        $stop.Count | Should -Be 3
        $stop[0].command | Should -Be '~/.cursor/skills/goal/goal-stop.sh'
        $stop[1].command | Should -Be './other.sh'
        $stop[2].command | Should -Be 'py -3 -u stop.py'
        $stop[2]._cursor_goal | Should -Be 'cursor_goal_stop_hook'
    }

    It 'creates hooks.stop when missing' {
        $data = [pscustomobject]@{ version = $null }
        $merged = Merge-GoalStopHook -Data $data -HookCommand 'python -u stop.py' -Marker 'cursor_goal_stop_hook'
        @($merged.hooks.stop).Count | Should -Be 1
        $merged.version | Should -Be 1
    }
}

Describe 'Invoke-GoalInstall' {
    BeforeEach {
        $script:TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-home-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
    }

    AfterEach {
        if (Test-Path $TempHome) {
            Remove-Item -Recurse -Force $TempHome
        }
    }

    It 'installs package, skill scripts, agent, and hooks.json' {
        $python = Find-GoalPython
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python $python
        $code | Should -Be 0

        $installDir = Join-Path $TempHome '.cursor\skills\cursor-goal'
        Test-Path (Join-Path $installDir 'cursor_goal\__init__.py') | Should -BeTrue
        Test-Path (Join-Path $installDir 'SKILL.md') | Should -BeTrue
        Test-Path (Join-Path $installDir 'scripts\stop_hook.py') | Should -BeTrue
        Test-Path (Join-Path $installDir 'scripts\stop_hook.cmd') | Should -BeTrue
        Test-Path (Join-Path $installDir 'scripts\run_goal.py') | Should -BeTrue
        Test-Path (Join-Path $TempHome '.cursor\agents\goalKeeper.md') | Should -BeTrue
        Test-Path (Join-Path $TempHome '.cursor\agents\goal-evaluator.md') | Should -BeTrue
        Test-Path (Join-Path $TempHome '.cursor\agents\goal-auditor.md') | Should -BeTrue
        Test-Path (Join-Path $TempHome '.cursor-goal\data') | Should -BeTrue

        $hooksPath = Join-Path $TempHome '.cursor\hooks.json'
        $hooks = Get-Content -Raw $hooksPath | ConvertFrom-Json
        @($hooks.hooks.stop).Count | Should -Be 1
        $hooks.hooks.stop[0]._cursor_goal | Should -Be 'cursor_goal_stop_hook'
        $hooks.hooks.stop[0].command | Should -Match 'stop_hook\.cmd'
        # Absolute path (drive letter or UNC)
        $hooks.hooks.stop[0].command | Should -Match '(?i)([a-z]:\\|\\\\)'
        @($hooks.hooks.subagentStop).Count | Should -Be 2
        $subPairs = @{}
        @($hooks.hooks.subagentStop) | ForEach-Object {
            $subPairs[$_._cursor_goal] = $_.matcher
        }
        $subPairs['cursor_goal_subagent_stop_hook'] | Should -Be 'goal-evaluator'
        $subPairs['cursor_goal_subagent_audit_stop_hook'] | Should -Be 'goal-auditor'
        $cmdBody = Get-Content -Raw (Join-Path $installDir 'scripts\stop_hook.cmd')
        $cmdBody | Should -Match 'PYTHONUNBUFFERED'
        $cmdBody | Should -Match 'stop_hook\.py'
        $cmdBody | Should -Match 'goto :use_cgp'
        $cmdBody | Should -Match '%CGP%'
        $cmdBody | Should -Match 'findstr /R'
        $cmdBody | Should -Match 'unsafe cmd metacharacters'
        $wakeCmd = Get-Content -Raw (Join-Path $installDir 'scripts\wake_loop.cmd')
        $wakeCmd | Should -Match 'goto :use_cgp'
        $wakeCmd | Should -Match '%CGP%'
        $wakeCmd | Should -Match 'findstr /R'
        Test-Path (Join-Path $installDir 'scripts\.tmp') | Should -BeTrue
        $bytes = [System.IO.File]::ReadAllBytes($hooksPath)
        if ($bytes.Length -ge 3) {
            $hasBom = ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
            $hasBom | Should -BeFalse
        }
    }

    It 'backs up existing agent markdown on reinstall' {
        $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
        $agentsDir = Join-Path $TempHome '.cursor\agents'
        Set-Content -Path (Join-Path $agentsDir 'goalKeeper.md') -Value 'old-keeper' -Encoding utf8
        Set-Content -Path (Join-Path $agentsDir 'goal-evaluator.md') -Value 'old-eval' -Encoding utf8
        Set-Content -Path (Join-Path $agentsDir 'goal-auditor.md') -Value 'old-audit' -Encoding utf8

        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
        $code | Should -Be 0
        $agentBakRoot = Join-Path $TempHome '.cursor-goal\backups\agents'
        Test-Path $agentBakRoot | Should -BeTrue
        $agentFiles = @(Get-ChildItem -LiteralPath $agentBakRoot -Recurse -File)
        @($agentFiles | Where-Object { $_.Name -eq 'goalKeeper.md' }).Count | Should -BeGreaterThan 0
        @($agentFiles | Where-Object { $_.Name -eq 'goal-evaluator.md' }).Count | Should -BeGreaterThan 0
        @($agentFiles | Where-Object { $_.Name -eq 'goal-auditor.md' }).Count | Should -BeGreaterThan 0
        @(Get-ChildItem -LiteralPath $agentsDir -Filter '*.bak.*' -ErrorAction SilentlyContinue).Count | Should -Be 0
        (Get-Content -Raw (Join-Path $agentsDir 'goalKeeper.md')).Trim() | Should -Not -Be 'old-keeper'
    }

    It 'aborts before skill copy when ACL harden fails' {
        Mock Protect-GoalDataDirAcl { return 1 }
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
        $code | Should -Be 1
        Test-Path (Join-Path $TempHome '.cursor\skills\cursor-goal\SKILL.md') | Should -BeFalse
    }

    It 'backs up and merges an existing hooks.json' {
        $cursorDir = Join-Path $TempHome '.cursor'
        New-Item -ItemType Directory -Force -Path $cursorDir | Out-Null
        $hooksPath = Join-Path $cursorDir 'hooks.json'
        @{
            version = 1
            hooks   = @{
                stop = @(
                    @{ command = './keep-me.sh' },
                    @{ command = 'python -u stop_hook.py' }
                )
            }
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $hooksPath -Encoding utf8

        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
        $code | Should -Be 0
        $hookBaks = @(Get-ChildItem -Path (Join-Path $TempHome '.cursor-goal\backups\hooks') -Filter 'hooks.json.bak.*' -File -ErrorAction SilentlyContinue)
        $hookBaks.Count | Should -BeGreaterThan 0
        @(Get-ChildItem -Path $cursorDir -Filter 'hooks.json.bak.*' -File -ErrorAction SilentlyContinue).Count | Should -Be 0
        $hooks = Get-Content -Raw $hooksPath | ConvertFrom-Json
        $cmds = @($hooks.hooks.stop | ForEach-Object { $_.command })
        $cmds | Should -Contain './keep-me.sh'
        (@($cmds | Where-Object { $_ -match 'stop_hook\.cmd' })).Count | Should -Be 1
    }

    It 'replaces a previous package install and removes legacy bash stubs' {
        $installDir = Join-Path $TempHome '.cursor\skills\cursor-goal'
        New-Item -ItemType Directory -Force -Path (Join-Path $installDir 'cursor_goal') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $installDir 'scripts') | Out-Null
        Set-Content -Path (Join-Path $installDir 'cursor_goal\old.txt') -Value 'old'
        Set-Content -Path (Join-Path $installDir 'SKILL.md') -Value 'old-skill'
        Set-Content -Path (Join-Path $installDir 'goal-stop.sh') -Value 'legacy'
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
        $code | Should -Be 0
        Test-Path (Join-Path $installDir 'cursor_goal\old.txt') | Should -BeFalse
        Test-Path (Join-Path $installDir 'goal-stop.sh') | Should -BeFalse
        Test-Path (Join-Path $installDir 'VERSION') | Should -BeTrue
        $skillBaks = @(Get-ChildItem -Path (Join-Path $TempHome '.cursor\skills') -Directory -Filter 'goal.bak.*' -ErrorAction SilentlyContinue)
        $skillBaks.Count | Should -Be 0
        $offRoot = @(Get-ChildItem -Path (Join-Path $TempHome '.cursor-goal\backups\skill') -Directory -ErrorAction SilentlyContinue)
        $offRoot.Count | Should -Be 1
    }

    It 'migrates leftover skills\goal and does not leave sibling bak folders' {
        $legacyDir = Join-Path $TempHome '.cursor\skills\goal'
        New-Item -ItemType Directory -Force -Path (Join-Path $legacyDir 'scripts') | Out-Null
        Set-Content -Path (Join-Path $legacyDir 'SKILL.md') -Value 'legacy-skill' -Encoding utf8
        $siblingBak = Join-Path $TempHome '.cursor\skills\goal.bak.20200101T000000Z'
        New-Item -ItemType Directory -Force -Path $siblingBak | Out-Null
        Set-Content -Path (Join-Path $siblingBak 'SKILL.md') -Value 'old-bak' -Encoding utf8
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
        $code | Should -Be 0
        Test-Path $legacyDir | Should -BeFalse
        Test-Path $siblingBak | Should -BeFalse
        Test-Path (Join-Path $TempHome '.cursor\skills\cursor-goal\SKILL.md') | Should -BeTrue
        @(Get-ChildItem -Path (Join-Path $TempHome '.cursor\skills') -Directory -Filter 'goal.bak.*' -ErrorAction SilentlyContinue).Count | Should -Be 0
        @(Get-ChildItem -Path (Join-Path $TempHome '.cursor-goal\backups\skill') -Directory -ErrorAction SilentlyContinue).Count | Should -Be 1
    }

    It 'returns 1 when Python discovery fails' {
        Mock Find-GoalPython { return $null }
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot
        $code | Should -Be 1
    }

    It 'returns 1 when package tree is missing' {
        $emptyRepo = Join-Path $TempHome 'empty-repo'
        New-Item -ItemType Directory -Force -Path $emptyRepo | Out-Null
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $emptyRepo -Python (Find-GoalPython)
        $code | Should -Be 1
    }

    It 'returns 1 when SKILL.md is missing but package exists' {
        $partial = Join-Path $TempHome 'partial-repo'
        $pkg = Join-Path $partial 'src\cursor_goal'
        New-Item -ItemType Directory -Force -Path $pkg | Out-Null
        Set-Content -Path (Join-Path $pkg '__init__.py') -Value '__version__ = "0.0.0"'
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $partial -Python (Find-GoalPython)
        $code | Should -Be 1
    }

    It 'returns 1 when goalKeeper agent is missing' {
        $partial = Join-Path $TempHome 'no-keeper'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial 'src\cursor_goal') | Out-Null
        Set-Content -Path (Join-Path $partial 'src\cursor_goal\__init__.py') -Value '__version__ = "0.0.0"'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial '.cursor\skills\cursor-goal') | Out-Null
        Set-Content -Path (Join-Path $partial '.cursor\skills\cursor-goal\SKILL.md') -Value 'skill'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial '.cursor\agents') | Out-Null
        Set-Content -Path (Join-Path $partial '.cursor\agents\goal-evaluator.md') -Value 'eval'
        Set-Content -Path (Join-Path $partial '.cursor\agents\goal-auditor.md') -Value 'audit'
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $partial -Python (Find-GoalPython)
        $code | Should -Be 1
    }

    It 'returns 1 when goal-evaluator agent is missing' {
        $partial = Join-Path $TempHome 'no-eval'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial 'src\cursor_goal') | Out-Null
        Set-Content -Path (Join-Path $partial 'src\cursor_goal\__init__.py') -Value '__version__ = "0.0.0"'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial '.cursor\skills\cursor-goal') | Out-Null
        Set-Content -Path (Join-Path $partial '.cursor\skills\cursor-goal\SKILL.md') -Value 'skill'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial '.cursor\agents') | Out-Null
        Set-Content -Path (Join-Path $partial '.cursor\agents\goalKeeper.md') -Value 'keeper'
        Set-Content -Path (Join-Path $partial '.cursor\agents\goal-auditor.md') -Value 'audit'
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $partial -Python (Find-GoalPython)
        $code | Should -Be 1
    }

    It 'returns 1 when goal-auditor agent is missing' {
        $partial = Join-Path $TempHome 'no-auditor'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial 'src\cursor_goal') | Out-Null
        Set-Content -Path (Join-Path $partial 'src\cursor_goal\__init__.py') -Value '__version__ = "0.0.0"'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial '.cursor\skills\cursor-goal') | Out-Null
        Set-Content -Path (Join-Path $partial '.cursor\skills\cursor-goal\SKILL.md') -Value 'skill'
        New-Item -ItemType Directory -Force -Path (Join-Path $partial '.cursor\agents') | Out-Null
        Set-Content -Path (Join-Path $partial '.cursor\agents\goalKeeper.md') -Value 'keeper'
        Set-Content -Path (Join-Path $partial '.cursor\agents\goal-evaluator.md') -Value 'eval'
        $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $partial -Python (Find-GoalPython)
        $code | Should -Be 1
    }
}

Describe 'Select-GoalStopHooksRemaining' {
    It 'removes only marked entries' {
        $data = [pscustomobject]@{
            hooks = [pscustomobject]@{
                stop = @(
                    [pscustomobject]@{ command = './keep.sh' },
                    [pscustomobject]@{ command = 'x'; _cursor_goal = 'cursor_goal_stop_hook' },
                    [pscustomobject]@{ command = 'my-tool --path=/opt/stop_hook.py' }
                )
            }
        }
        $cleaned = Select-GoalStopHooksRemaining -Data $data -Marker 'cursor_goal_stop_hook'
        @($cleaned.hooks.stop).Count | Should -Be 2
        $cleaned.hooks.stop[0].command | Should -Be './keep.sh'
        $cleaned.hooks.stop[1].command | Should -Be 'my-tool --path=/opt/stop_hook.py'
    }

    It 'leaves unmarked stop_hook entries when no marker is present' {
        $data = [pscustomobject]@{
            hooks = [pscustomobject]@{
                stop = @(
                    [pscustomobject]@{ command = './keep.sh' },
                    [pscustomobject]@{ command = 'cursor-goal stop' },
                    [pscustomobject]@{ command = 'python stop_hook.py' }
                )
            }
        }
        $cleaned = Select-GoalStopHooksRemaining -Data $data -Marker 'cursor_goal_stop_hook'
        @($cleaned.hooks.stop).Count | Should -Be 3
    }
}

Describe 'Invoke-GoalUninstall' {
    BeforeEach {
        $script:TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-un-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
    }

    AfterEach {
        if (Test-Path $TempHome) {
            Remove-Item -Recurse -Force $TempHome
        }
    }

    It 'removes skill, agents, and stop hooks but keeps data by default' {
        $code = Invoke-GoalUninstall -HomeDir $TempHome
        $code | Should -Be 0
        Test-Path (Join-Path $TempHome '.cursor\skills\cursor-goal') | Should -BeFalse
        Test-Path (Join-Path $TempHome '.cursor\skills\goal') | Should -BeFalse
        Test-Path (Join-Path $TempHome '.cursor\agents\goalKeeper.md') | Should -BeFalse
        Test-Path (Join-Path $TempHome '.cursor\agents\goal-evaluator.md') | Should -BeFalse
        Test-Path (Join-Path $TempHome '.cursor\agents\goal-auditor.md') | Should -BeFalse
        Test-Path (Join-Path $TempHome '.cursor-goal') | Should -BeTrue
        Test-Path (Join-Path $TempHome '.cursor-goal\backups') | Should -BeFalse
        $hooks = Get-Content -Raw (Join-Path $TempHome '.cursor\hooks.json') | ConvertFrom-Json
        @($hooks.hooks.stop).Count | Should -Be 0
    }

    It 'removes installer backup debris' {
        $skillsParent = Join-Path $TempHome '.cursor\skills'
        $bak = Join-Path $skillsParent 'goal.bak.20200101T000000Z'
        New-Item -ItemType Directory -Force -Path $bak | Out-Null
        Set-Content -Path (Join-Path $bak 'SKILL.md') -Value 'old' -Encoding utf8
        $legacySkill = Join-Path $skillsParent 'goal'
        New-Item -ItemType Directory -Force -Path $legacySkill | Out-Null
        Set-Content -Path (Join-Path $legacySkill 'SKILL.md') -Value 'legacy' -Encoding utf8
        $offRoot = Join-Path $TempHome '.cursor-goal\backups\skill\keep-me'
        New-Item -ItemType Directory -Force -Path $offRoot | Out-Null
        $agentsDir = Join-Path $TempHome '.cursor\agents'
        Set-Content -Path (Join-Path $agentsDir 'goalKeeper.md.bak.20200101T000000Z') -Value 'x' -Encoding utf8
        Set-Content -Path (Join-Path $agentsDir 'goal-evaluator.md.bak.20200101T000000Z') -Value 'y' -Encoding utf8
        Set-Content -Path (Join-Path $agentsDir 'goal-auditor.md.bak.20200101T000000Z') -Value 'z' -Encoding utf8
        $code = Invoke-GoalUninstall -HomeDir $TempHome
        $code | Should -Be 0
        Test-Path -LiteralPath $bak | Should -BeFalse
        Test-Path -LiteralPath $legacySkill | Should -BeFalse
        Test-Path -LiteralPath (Join-Path $TempHome '.cursor-goal\backups') | Should -BeFalse
        Test-Path -LiteralPath (Join-Path $agentsDir 'goalKeeper.md.bak.20200101T000000Z') | Should -BeFalse
        Test-Path -LiteralPath (Join-Path $agentsDir 'goal-evaluator.md.bak.20200101T000000Z') | Should -BeFalse
        Test-Path -LiteralPath (Join-Path $agentsDir 'goal-auditor.md.bak.20200101T000000Z') | Should -BeFalse
    }

    It 'purges data when -PurgeData is set' {
        $code = Invoke-GoalUninstall -HomeDir $TempHome -PurgeData
        $code | Should -Be 0
        Test-Path (Join-Path $TempHome '.cursor-goal') | Should -BeFalse
    }

    It 'is idempotent when already uninstalled' {
        $null = Invoke-GoalUninstall -HomeDir $TempHome -PurgeData
        $code = Invoke-GoalUninstall -HomeDir $TempHome -PurgeData
        $code | Should -Be 0
    }
}

Describe 'Write helpers' {
    It 'emit info warn and error without throwing' {
        { Write-GoalInfo 'ok' } | Should -Not -Throw
        { Write-GoalWarn 'warn' } | Should -Not -Throw
        { Write-GoalErr 'err' } | Should -Not -Throw
    }
}

Describe 'Write-Utf8NoBomFile' {
    It 'writes UTF-8 without BOM' {
        $path = Join-Path ([IO.Path]::GetTempPath()) ("cg-bom-" + [guid]::NewGuid().ToString('N') + '.json')
        try {
            Write-Utf8NoBomFile -Path $path -Content "{`"ok`":true}`n"
            $bytes = [System.IO.File]::ReadAllBytes($path)
            ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) | Should -BeFalse
        }
        finally {
            if (Test-Path $path) { Remove-Item -Force $path }
        }
    }
}

Describe 'Invoke-GoalHooksConfigMerge failure' {
    It 'returns non-zero to the installer when merge helper fails' {
        Mock Invoke-GoalHooksConfigMerge { return 1 }
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-mergefail-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            $code | Should -Be 1
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }

    It 'restores hooks.json and skill backup when merge fails after upgrade' {
        Mock Invoke-GoalHooksConfigMerge { return 1 }
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-mergerestore-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            # Seed a prior install + hooks so backups exist and restore paths run.
            $installDir = Join-Path $TempHome '.cursor\skills\cursor-goal'
            New-Item -ItemType Directory -Force -Path (Join-Path $installDir 'scripts') | Out-Null
            Set-Content -Path (Join-Path $installDir 'SKILL.md') -Value 'prior-skill'
            Set-Content -Path (Join-Path $installDir 'scripts\marker.txt') -Value 'prior'
            $hooksPath = Join-Path $TempHome '.cursor\hooks.json'
            New-Item -ItemType Directory -Force -Path (Split-Path $hooksPath) | Out-Null
            @{
                version = 1
                hooks   = @{
                    stop = @(@{ command = './prior-keep.sh' })
                }
            } | ConvertTo-Json -Depth 10 | Set-Content -Path $hooksPath -Encoding utf8

            $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            $code | Should -Be 1
            Test-Path (Join-Path $installDir 'SKILL.md') | Should -BeTrue
            (Get-Content -Raw (Join-Path $installDir 'SKILL.md')).Trim() | Should -Be 'prior-skill'
            $hooks = Get-Content -Raw $hooksPath | ConvertFrom-Json
            $cmds = @($hooks.hooks.stop | ForEach-Object { $_.command })
            $cmds | Should -Contain './prior-keep.sh'
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }
}

Describe 'Invoke-GoalUninstall python fallback' {
    It 'uses inline Python JSON cleanup when package hooks_config cannot run' {
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-psun-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            # Remove package so hooks_config path is unavailable; force inline Python cleanup.
            Remove-Item -Recurse -Force (Join-Path $TempHome '.cursor\skills\cursor-goal\cursor_goal')
            $code = Invoke-GoalUninstall -HomeDir $TempHome
            $code | Should -Be 0
            $hooks = Get-Content -Raw (Join-Path $TempHome '.cursor\hooks.json') | ConvertFrom-Json
            @($hooks.hooks.stop).Count | Should -Be 0
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }

    It 'prefers python when py launcher is unavailable' {
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-pyfallback-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            $pythonExe = (Find-GoalPython).Exe
            Mock Get-Command {
                param($Name, $ErrorAction)
                if ($Name -eq 'py') { return $null }
                if ($Name -eq 'python') {
                    return [pscustomobject]@{ Name = 'python'; Source = $pythonExe }
                }
                return $null
            }
            $code = Invoke-GoalUninstall -HomeDir $TempHome
            $code | Should -Be 0
            Test-Path (Join-Path $TempHome '.cursor\skills\cursor-goal') | Should -BeFalse
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }

    It 'keeps skill tree when hook cleanup cannot run' {
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-unhooks-fail-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            # Remove package and block both py/python so neither hooks_config nor
            # inline JSON cleanup can run — uninstall must abort and keep the skill.
            Remove-Item -Recurse -Force (Join-Path $TempHome '.cursor\skills\cursor-goal\cursor_goal')
            Mock Get-Command { return $null }
            $code = Invoke-GoalUninstall -HomeDir $TempHome
            $code | Should -Be 1
            Test-Path (Join-Path $TempHome '.cursor\skills\cursor-goal') | Should -BeTrue
            $hooks = Get-Content -Raw (Join-Path $TempHome '.cursor\hooks.json') | ConvertFrom-Json
            @($hooks.hooks.stop).Count | Should -BeGreaterThan 0
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }
}

Describe 'Find-GoalPython failure path' {
    It 'returns null when no interpreter is available' {
        Mock Get-Command { return $null }
        Find-GoalPython | Should -BeNullOrEmpty
    }
}

Describe 'Invoke-GoalInstall default repo root' {
    BeforeEach {
        $script:TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-root-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
    }

    AfterEach {
        if (Test-Path $TempHome) {
            Remove-Item -Recurse -Force $TempHome
        }
    }

    It 'resolves RepoRoot from the script location when omitted' {
        # From the dotsourced install script, resolving relative to tests/powershell still
        # reaches the package via ..\.. when MyInvocation/PSCommandPath points at the test file.
        $code = Invoke-GoalInstall -HomeDir $TempHome -Python (Find-GoalPython)
        # Accept either success (repo found) or package-missing (path resolved elsewhere).
        $code | Should -Be 0
    }
}

Describe 'Test-GoalManagedAgentFile' {
    It 'returns false when the file does not exist' {
        $missing = Join-Path ([IO.Path]::GetTempPath()) ("cg-agent-missing-" + [guid]::NewGuid().ToString('N') + '.md')
        Test-GoalManagedAgentFile -Path $missing | Should -BeFalse
    }

    It 'returns false for a foreign file lacking the provenance marker' {
        $path = Join-Path ([IO.Path]::GetTempPath()) ("cg-agent-foreign-" + [guid]::NewGuid().ToString('N') + '.md')
        try {
            Set-Content -Path $path -Value '# hand-authored agent, not ours' -Encoding utf8
            Test-GoalManagedAgentFile -Path $path | Should -BeFalse
        }
        finally {
            if (Test-Path $path) { Remove-Item -Force $path }
        }
    }

    It 'returns true for a file carrying the provenance marker' {
        $path = Join-Path ([IO.Path]::GetTempPath()) ("cg-agent-managed-" + [guid]::NewGuid().ToString('N') + '.md')
        try {
            Set-Content -Path $path -Value "<!-- cursor-goal:managed-agent -->`n# agent" -Encoding utf8
            Test-GoalManagedAgentFile -Path $path | Should -BeTrue
        }
        finally {
            if (Test-Path $path) { Remove-Item -Force $path }
        }
    }
}

Describe 'Invoke-GoalInstall rollback restores prior agent files' {
    It 'restores pre-existing goalKeeper.md, goal-evaluator.md, and goal-auditor.md when the hook merge fails' {
        Mock Invoke-GoalHooksConfigMerge { return 1 }
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-agentrestore-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            $agentsDir = Join-Path $TempHome '.cursor\agents'
            New-Item -ItemType Directory -Force -Path $agentsDir | Out-Null
            $priorKeeper = "<!-- cursor-goal:managed-agent -->`nprior keeper v1"
            $priorEvaluator = "<!-- cursor-goal:managed-agent -->`nprior evaluator v1"
            $priorAuditor = "<!-- cursor-goal:managed-agent -->`nprior auditor v1"
            Set-Content -Path (Join-Path $agentsDir 'goalKeeper.md') -Value $priorKeeper -Encoding utf8
            Set-Content -Path (Join-Path $agentsDir 'goal-evaluator.md') -Value $priorEvaluator -Encoding utf8
            Set-Content -Path (Join-Path $agentsDir 'goal-auditor.md') -Value $priorAuditor -Encoding utf8

            $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            $code | Should -Be 1

            (Get-Content -Raw (Join-Path $agentsDir 'goalKeeper.md')).Trim() | Should -Be $priorKeeper
            (Get-Content -Raw (Join-Path $agentsDir 'goal-evaluator.md')).Trim() | Should -Be $priorEvaluator
            (Get-Content -Raw (Join-Path $agentsDir 'goal-auditor.md')).Trim() | Should -Be $priorAuditor
            Get-ChildItem -LiteralPath $agentsDir -Filter '*.bak.*' | Should -BeNullOrEmpty
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }
}

Describe 'Invoke-GoalInstall doctor failure' {
    It 'returns 1 and leaves install files in place when manage doctor fails' {
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-doctorfail-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        $env:CURSOR_GOAL_PYTHON = 'C:\bad" & calc.exe'
        try {
            $code = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            $code | Should -Be 1
            Test-Path (Join-Path $TempHome '.cursor\skills\cursor-goal\SKILL.md') | Should -BeTrue
        }
        finally {
            Remove-Item Env:CURSOR_GOAL_PYTHON -ErrorAction SilentlyContinue
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }
}

Describe 'Invoke-GoalUninstall inline fallback with py launcher unavailable' {
    It 'falls back to python for the inline-JSON cleanup path when the package is missing' {
        $TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-pyfallback2-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        try {
            $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
            $pythonExe = (Find-GoalPython).Exe
            # Removing the package forces the second (package-independent) cleanup
            # path, which has its own py/python resolution to exercise.
            Remove-Item -Recurse -Force (Join-Path $TempHome '.cursor\skills\cursor-goal\cursor_goal')
            Mock Get-Command {
                param($Name, $ErrorAction)
                if ($Name -eq 'py') { return $null }
                if ($Name -eq 'python') {
                    return [pscustomobject]@{ Name = 'python'; Source = $pythonExe }
                }
                return $null
            }
            $code = Invoke-GoalUninstall -HomeDir $TempHome
            $code | Should -Be 0
            $hooks = Get-Content -Raw (Join-Path $TempHome '.cursor\hooks.json') | ConvertFrom-Json
            @($hooks.hooks.stop).Count | Should -Be 0
        }
        finally {
            if (Test-Path $TempHome) { Remove-Item -Recurse -Force $TempHome }
        }
    }
}

Describe 'Invoke-GoalUninstall leaves foreign or backup artifacts as documented' {
    BeforeEach {
        $script:TempHome = Join-Path ([IO.Path]::GetTempPath()) ("cg-unforeign-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $TempHome | Out-Null
        $null = Invoke-GoalInstall -HomeDir $TempHome -RepoRoot $RepoRoot -Python (Find-GoalPython)
    }

    AfterEach {
        if (Test-Path $TempHome) {
            Remove-Item -Recurse -Force $TempHome
        }
    }

    It 'leaves hand-edited agent files in place (missing provenance marker)' {
        $agentsDir = Join-Path $TempHome '.cursor\agents'
        Set-Content -Path (Join-Path $agentsDir 'goalKeeper.md') -Value 'hand-edited, no marker' -Encoding utf8
        Set-Content -Path (Join-Path $agentsDir 'goal-evaluator.md') -Value 'hand-edited, no marker' -Encoding utf8
        Set-Content -Path (Join-Path $agentsDir 'goal-auditor.md') -Value 'hand-edited, no marker' -Encoding utf8
        $code = Invoke-GoalUninstall -HomeDir $TempHome
        $code | Should -Be 0
        Test-Path (Join-Path $agentsDir 'goalKeeper.md') | Should -BeTrue
        Test-Path (Join-Path $agentsDir 'goal-evaluator.md') | Should -BeTrue
        Test-Path (Join-Path $agentsDir 'goal-auditor.md') | Should -BeTrue
    }

    It 'removes stale hooks.json.bak.* backup files' {
        $hooksBak = Join-Path $TempHome '.cursor\hooks.json.bak.20200101T000000Z'
        Set-Content -Path $hooksBak -Value '{}' -Encoding utf8
        $code = Invoke-GoalUninstall -HomeDir $TempHome
        $code | Should -Be 0
        Test-Path -LiteralPath $hooksBak | Should -BeFalse
    }
}
