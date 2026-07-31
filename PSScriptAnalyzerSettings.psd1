# PSScriptAnalyzer settings for cursor-goal installer scripts.
#
# Write-Host is intentional: these are user-facing CLI installers that need
# colored console output (not pipeline objects).
# UTF-8 without BOM + LF is required for cross-platform Git checkout; BOM
# would break some Unix tooling when the same tree is used under WSL.

@{
    Severity     = @('Error', 'Warning', 'Information')
    ExcludeRules = @(
        'PSAvoidUsingWriteHost',
        'PSUseBOMForUnicodeEncodedFile'
    )
}
