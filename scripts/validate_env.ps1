param(
    [string]$EnvFile = '.env.production.template'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-Host "[FAIL] Env file not found: $EnvFile" -ForegroundColor Red
    exit 1
}

$requiredKeys = @(
    'SECRET_KEY',
    'ASR_AUDIT_EXPORT_SIGNING_SECRET',
    'ADMIN_API_TOKEN',
    'ANALYST_API_TOKEN',
    'AUDITOR_API_TOKEN',
    'LANGUAGE_ASR_PROVIDER',
    'LANGUAGE_ASR_STRICT_MODE',
    'USE_REAL_BHASHINI_ASR',
    'BHASHINI_ASR_ENDPOINT',
    'BHASHINI_ASR_API_KEY',
    'BHASHINI_ASR_TIMEOUT_SECONDS',
    'USE_REAL_AI4BHARAT_ASR',
    'AI4BHARAT_ASR_ENDPOINT',
    'AI4BHARAT_ASR_API_KEY',
    'AI4BHARAT_ASR_TIMEOUT_SECONDS',
    'LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED',
    'LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD',
    'LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS'
)

$rawLines = Get-Content -LiteralPath $EnvFile
$entries = @{}

foreach ($line in $rawLines) {
    $trimmed = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
    if ($trimmed.StartsWith('#')) { continue }

    $idx = $trimmed.IndexOf('=')
    if ($idx -lt 1) { continue }

    $key = $trimmed.Substring(0, $idx).Trim()
    $value = $trimmed.Substring($idx + 1).Trim()

    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    $entries[$key] = $value
}

function Test-UnsetValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }

    $v = $Value.Trim()
    if ($v -match '^<[^>]+>$') { return $true }
    if ($v -match '(?i)your_|change_me|replace_me|example|placeholder') { return $true }
    if ($v -eq 'visbharat-admin-token' -or $v -eq 'visbharat-analyst-token' -or $v -eq 'visbharat-auditor-token') { return $true }

    return $false
}

$failures = New-Object System.Collections.Generic.List[string]

foreach ($key in $requiredKeys) {
    if (-not $entries.ContainsKey($key)) {
        $failures.Add("${key}: missing")
        continue
    }

    $value = [string]$entries[$key]
    if (Test-UnsetValue -Value $value) {
        $failures.Add("${key}: unset/placeholder")
    }
}

$provider = ''
if ($entries.ContainsKey('LANGUAGE_ASR_PROVIDER')) {
    $provider = ([string]$entries['LANGUAGE_ASR_PROVIDER']).Trim().ToLowerInvariant()
}

if ($provider -notin @('google', 'bhashini', 'simulation')) {
    $failures.Add('LANGUAGE_ASR_PROVIDER: must be one of google|bhashini|simulation')
}

if ($entries.ContainsKey('USE_REAL_BHASHINI_ASR') -and ([string]$entries['USE_REAL_BHASHINI_ASR']).Trim().ToLowerInvariant() -eq 'true') {
    foreach ($k in @('BHASHINI_ASR_ENDPOINT', 'BHASHINI_ASR_API_KEY')) {
        if (-not $entries.ContainsKey($k) -or (Test-UnsetValue -Value ([string]$entries[$k])) ) {
            $failures.Add("${k}: required when USE_REAL_BHASHINI_ASR=true")
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Host "[FAIL] Environment validation failed for $EnvFile" -ForegroundColor Red
    $failures | Sort-Object | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    exit 1
}

Write-Host "[PASS] Environment validation passed for $EnvFile" -ForegroundColor Green
exit 0

