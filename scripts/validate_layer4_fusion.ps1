param(
    [string]$SeccData = 'docs/release/layer4_secc.sample.json',
    [string]$CensusData = 'docs/release/layer4_census.sample.csv',
    [string]$NfhsData = 'docs/release/layer4_nfhs.sample.json',
    [string]$SdgData = 'docs/release/layer4_sdg_india_index.sample.json',
    [string]$MpiData = 'docs/release/layer4_niti_mpi.sample.json',
    [string]$AspirationalData = 'docs/release/layer4_aspirational_districts.sample.json',
    [string]$GatiData = 'docs/release/layer4_pm_gati_shakti.sample.json',
    [string]$BudgetData = 'docs/release/layer4_budget_outlays.sample.json'
)

$ErrorActionPreference = 'Stop'

function Read-JsonList {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "File not found: $Path"
    }

    $raw = Get-Content -LiteralPath $Path -Raw
    try {
        $obj = $raw | ConvertFrom-Json
    }
    catch {
        throw "Invalid JSON in ${Path}: $($_.Exception.Message)"
    }

    if ($null -eq $obj) {
        throw "JSON payload is null: $Path"
    }

    $arr = @($obj)
    if ($arr.Count -lt 1) {
        throw "JSON list must contain at least one record: $Path"
    }

    return $arr
}

function Read-CsvRows {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "File not found: $Path"
    }

    $rows = Import-Csv -LiteralPath $Path
    if ($null -eq $rows -or @($rows).Count -lt 1) {
        throw "CSV must contain at least one row: $Path"
    }
    return @($rows)
}

function Is-Number {
    param($Value)
    return $Value -is [byte] -or $Value -is [sbyte] -or $Value -is [int16] -or $Value -is [int32] -or $Value -is [int64] -or $Value -is [uint16] -or $Value -is [uint32] -or $Value -is [uint64] -or $Value -is [single] -or $Value -is [double] -or $Value -is [decimal] -or (($Value -is [string]) -and ($Value -match '^-?\d+(\.\d+)?$'))
}

function Assert-RequiredText {
    param($Row, [string]$Field, [string]$Context)
    if (-not $Row.PSObject.Properties.Name.Contains($Field)) {
        throw "$Context missing required field: $Field"
    }
    if ([string]::IsNullOrWhiteSpace([string]$Row.$Field)) {
        throw "$Context required field is empty: $Field"
    }
}

function Assert-RequiredNumber {
    param($Row, [string]$Field, [string]$Context)
    if (-not $Row.PSObject.Properties.Name.Contains($Field)) {
        throw "$Context missing required numeric field: $Field"
    }
    if (-not (Is-Number $Row.$Field)) {
        throw "$Context field must be numeric: $Field"
    }
}

function Validate-DistrictStateBase {
    param($Rows, [string]$Label)
    for ($i = 0; $i -lt $Rows.Count; $i++) {
        $row = $Rows[$i]
        $ctx = "$Label row $i"
        Assert-RequiredText -Row $row -Field 'district' -Context $ctx
        Assert-RequiredText -Row $row -Field 'state' -Context $ctx
    }
}

try {
    $secc = Read-JsonList -Path $SeccData
    Validate-DistrictStateBase -Rows $secc -Label 'SECC'

    $census = Read-CsvRows -Path $CensusData
    Validate-DistrictStateBase -Rows $census -Label 'Census'
    for ($i = 0; $i -lt $census.Count; $i++) {
        Assert-RequiredNumber -Row $census[$i] -Field 'population' -Context "Census row $i"
    }

    $nfhs = Read-JsonList -Path $NfhsData
    Validate-DistrictStateBase -Rows $nfhs -Label 'NFHS'

    $sdg = Read-JsonList -Path $SdgData
    Validate-DistrictStateBase -Rows $sdg -Label 'SDG'

    $mpi = Read-JsonList -Path $MpiData
    Validate-DistrictStateBase -Rows $mpi -Label 'NITI_MPI'

    $asp = Read-JsonList -Path $AspirationalData
    Validate-DistrictStateBase -Rows $asp -Label 'AspirationalDistricts'

    $gati = Read-JsonList -Path $GatiData
    Validate-DistrictStateBase -Rows $gati -Label 'GatiShakti'

    $budget = Read-JsonList -Path $BudgetData
    Validate-DistrictStateBase -Rows $budget -Label 'BudgetOutlays'
    for ($i = 0; $i -lt $budget.Count; $i++) {
        Assert-RequiredNumber -Row $budget[$i] -Field 'total_outlay_lakh' -Context "BudgetOutlays row $i"
    }

    Write-Host '[PASS] Layer 4 fusion packs validated successfully.' -ForegroundColor Green
    Write-Host "  SECC:               $SeccData"
    Write-Host "  Census:             $CensusData"
    Write-Host "  NFHS:               $NfhsData"
    Write-Host "  SDG India Index:    $SdgData"
    Write-Host "  NITI MPI:           $MpiData"
    Write-Host "  Aspirational:       $AspirationalData"
    Write-Host "  PM Gati Shakti:     $GatiData"
    Write-Host "  Budget Outlays:     $BudgetData"
    exit 0
}
catch {
    Write-Host '[FAIL] Layer 4 fusion pack validation failed.' -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
