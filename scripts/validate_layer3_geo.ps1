param(
    [string]$PinData = 'docs/release/pin_geocode_index.sample.json',
    [string]$WardData = 'docs/release/ward_polygons.sample.json',
    [string]$SvamitvaData = 'docs/release/svamitva_village_maps.sample.json',
    [string]$PinSchema = 'docs/release/pin_geocode_index.schema.json',
    [string]$WardSchema = 'docs/release/ward_polygons.schema.json',
    [string]$SvamitvaSchema = 'docs/release/svamitva_village_maps.schema.json'
)

$ErrorActionPreference = 'Stop'

function Read-JsonFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "File not found: $Path"
    }

    $raw = Get-Content -LiteralPath $Path -Raw
    try {
        return $raw | ConvertFrom-Json
    }
    catch {
        throw "Invalid JSON in ${Path}: $($_.Exception.Message)"
    }
}

function Is-Number {
    param($Value)
    return $Value -is [byte] -or $Value -is [sbyte] -or $Value -is [int16] -or $Value -is [int32] -or $Value -is [int64] -or $Value -is [uint16] -or $Value -is [uint32] -or $Value -is [uint64] -or $Value -is [single] -or $Value -is [double] -or $Value -is [decimal]
}

function Assert-LatLng {
    param($Point, [string]$Context)

    if ($null -eq $Point -or -not $Point.PSObject.Properties.Name.Contains('lat') -or -not $Point.PSObject.Properties.Name.Contains('lng')) {
        throw "$Context missing lat/lng"
    }

    if (-not (Is-Number $Point.lat) -or -not (Is-Number $Point.lng)) {
        throw "$Context lat/lng must be numeric"
    }

    if ([double]$Point.lat -lt -90 -or [double]$Point.lat -gt 90) {
        throw "$Context lat out of range"
    }

    if ([double]$Point.lng -lt -180 -or [double]$Point.lng -gt 180) {
        throw "$Context lng out of range"
    }
}

function Validate-PinPack {
    param($Obj)

    if ($null -eq $Obj) { throw 'PIN pack root is null' }
    $props = @($Obj.PSObject.Properties)
    if ($props.Count -lt 1) { throw 'PIN pack must contain at least one entry' }

    foreach ($p in $props) {
        $key = [string]$p.Name
        if ($key -notmatch '^[0-9]{3,6}$') {
            throw "PIN key invalid: $key"
        }

        $entry = $p.Value
        foreach ($req in @('lat', 'lng', 'district')) {
            if (-not $entry.PSObject.Properties.Name.Contains($req)) {
                throw "PIN $key missing required field: $req"
            }
        }

        Assert-LatLng -Point $entry -Context "PIN $key"
        if ([string]::IsNullOrWhiteSpace([string]$entry.district)) {
            throw "PIN $key district cannot be empty"
        }
    }
}

function Validate-PolygonPack {
    param($Obj, [string]$Label, [string]$NameField)

    if ($null -eq $Obj) { throw "$Label root is null" }
    $districts = @($Obj.PSObject.Properties)
    if ($districts.Count -lt 1) { throw "$Label must contain at least one district" }

    foreach ($d in $districts) {
        $district = [string]$d.Name
        $items = @($d.Value)
        if ($items.Count -lt 1) {
            throw "$Label district $district must contain at least one polygon"
        }

        for ($i = 0; $i -lt $items.Count; $i++) {
            $item = $items[$i]
            if (-not $item.PSObject.Properties.Name.Contains($NameField)) {
                throw "$Label district $district item $i missing $NameField"
            }
            if ([string]::IsNullOrWhiteSpace([string]$item.$NameField)) {
                throw "$Label district $district item $i has empty $NameField"
            }
            if (-not $item.PSObject.Properties.Name.Contains('points')) {
                throw "$Label district $district item $i missing points"
            }
            $points = @($item.points)
            if ($points.Count -lt 3) {
                throw "$Label district $district item $i requires at least 3 points"
            }

            for ($j = 0; $j -lt $points.Count; $j++) {
                Assert-LatLng -Point $points[$j] -Context "$Label district $district item $i point $j"
            }
        }
    }
}

try {
    # Ensure all declared schema files are present and valid JSON (contract files)
    $null = Read-JsonFile -Path $PinSchema
    $null = Read-JsonFile -Path $WardSchema
    $null = Read-JsonFile -Path $SvamitvaSchema

    $pin = Read-JsonFile -Path $PinData
    $ward = Read-JsonFile -Path $WardData
    $sv = Read-JsonFile -Path $SvamitvaData

    Validate-PinPack -Obj $pin
    Validate-PolygonPack -Obj $ward -Label 'WARD_POLYGONS' -NameField 'ward'
    Validate-PolygonPack -Obj $sv -Label 'SVAMITVA_VILLAGE_MAPS' -NameField 'village'

    Write-Host "[PASS] Layer 3 geo packs validated successfully." -ForegroundColor Green
    Write-Host "  PIN data:       $PinData"
    Write-Host "  Ward polygons:  $WardData"
    Write-Host "  SVAMITVA maps:  $SvamitvaData"
    exit 0
}
catch {
    Write-Host "[FAIL] Layer 3 geo validation failed." -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}


