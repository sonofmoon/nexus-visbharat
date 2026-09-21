param(
    [string]$Layer3Script = 'scripts/validate_layer3_geo.ps1',
    [string]$Layer4Script = 'scripts/validate_layer4_fusion.ps1'
)

$ErrorActionPreference = 'Stop'

function Run-Validator {
    param(
        [string]$Name,
        [string]$ScriptPath
    )

    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        return [pscustomobject]@{
            Name = $Name
            Script = $ScriptPath
            ExitCode = 1
            Passed = $false
            Message = 'script not found'
        }
    }

    # Stream child output to console, but do not let it pollute return pipeline.
    & powershell -ExecutionPolicy Bypass -File $ScriptPath | Out-Host
    $code = $LASTEXITCODE

    return [pscustomobject]@{
        Name = $Name
        Script = $ScriptPath
        ExitCode = $code
        Passed = ($code -eq 0)
        Message = if ($code -eq 0) { 'ok' } else { 'failed' }
    }
}

$results = @(
    Run-Validator -Name 'Layer3Geo' -ScriptPath $Layer3Script
    Run-Validator -Name 'Layer4Fusion' -ScriptPath $Layer4Script
)

Write-Host ''
Write-Host '=== Data Pack Validation Summary ==='
foreach ($r in $results) {
    $status = if ($r.Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($r.Passed) { 'Green' } else { 'Red' }
    Write-Host ("[{0}] {1} ({2})" -f $status, $r.Name, $r.Script) -ForegroundColor $color
}

$failed = @($results | Where-Object { -not $_.Passed })
if ($failed.Count -gt 0) {
    Write-Host ''
    Write-Host ('[FAIL] One or more validators failed ({0}/{1}).' -f $failed.Count, $results.Count) -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host ('[PASS] All validators passed ({0}/{0}).' -f $results.Count) -ForegroundColor Green
exit 0
