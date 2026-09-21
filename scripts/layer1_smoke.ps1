$ErrorActionPreference = 'Stop'

param(
    [string]$BaseUrl,
    [string]$WebhookToken,
    [string]$WhatsAppVerifyToken,
    [string]$AdminApiToken,
    [string]$District
)

if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = $(if ($env:BASE_URL) { $env:BASE_URL } else { 'http://localhost:5000' }) }
if ([string]::IsNullOrWhiteSpace($WebhookToken)) { $WebhookToken = $(if ($env:WEBHOOK_SHARED_TOKEN) { $env:WEBHOOK_SHARED_TOKEN } else { 'visbharat-webhook-token' }) }
if ([string]::IsNullOrWhiteSpace($WhatsAppVerifyToken)) { $WhatsAppVerifyToken = $(if ($env:WHATSAPP_VERIFY_TOKEN) { $env:WHATSAPP_VERIFY_TOKEN } else { 'visbharat-whatsapp-verify' }) }
if ([string]::IsNullOrWhiteSpace($AdminApiToken)) { $AdminApiToken = $(if ($env:ADMIN_API_TOKEN) { $env:ADMIN_API_TOKEN } else { 'visbharat-admin-token' }) }
if ($null -eq $District) { $District = $(if ($env:DISTRICT) { $env:DISTRICT } else { '' }) }

function Resolve-District {
    param([string]$CurrentDistrict, [string]$Url)
    if (-not [string]::IsNullOrWhiteSpace($CurrentDistrict)) { return $CurrentDistrict }
    try {
        $resp = Invoke-WebRequest -Uri "$Url/api/districts" -Method Get -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            $json = $resp.Content | ConvertFrom-Json
            if ($json -is [System.Array] -and $json.Count -gt 0) {
                if ($json[0] -is [string]) { return [string]$json[0] }
                if ($json[0].district) { return [string]$json[0].district }
                if ($json[0].name) { return [string]$json[0].name }
            }
            if ($json.districts -and $json.districts.Count -gt 0) {
                if ($json.districts[0] -is [string]) { return [string]$json.districts[0] }
                if ($json.districts[0].district) { return [string]$json.districts[0].district }
                if ($json.districts[0].name) { return [string]$json.districts[0].name }
            }
        }
    }
    catch {}
    return 'Chennai'
}

function Add-Result {
    param([ref]$Results, [string]$Name, [int]$Http, [bool]$Pass, [string]$Note)
    $Results.Value += [pscustomobject]@{
        Check  = $Name
        Http   = $Http
        Result = $(if ($Pass) { 'PASS' } else { 'FAIL' })
        Note   = $Note
    }
}

function Invoke-JsonCheck {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Url,
        [hashtable]$Payload,
        [string]$AuthMode,
        [string]$WebhookToken,
        [string]$AdminApiToken,
        [ref]$Results
    )
    $headers = @{ 'Content-Type' = 'application/json' }
    if ($AuthMode -eq 'webhook') { $headers['X-Webhook-Token'] = $WebhookToken }
    elseif ($AuthMode -eq 'admin') { $headers['Authorization'] = "Bearer $AdminApiToken" }

    $status = 0
    $success = $false
    try {
        $response = Invoke-WebRequest -Uri $Url -Method $Method -Headers $headers -Body ($Payload | ConvertTo-Json -Depth 8 -Compress) -UseBasicParsing
        $status = [int]$response.StatusCode
        $obj = $null
        try { $obj = $response.Content | ConvertFrom-Json } catch {}
        if ($obj -and ($obj.PSObject.Properties.Name -contains 'success')) {
            $success = [bool]$obj.success
        }
        else {
            $success = ($status -eq 200)
        }
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        else {
            $status = 0
        }
    }
    Add-Result -Results $Results -Name $Name -Http $status -Pass (($status -eq 200) -and $success) -Note "success=$success"
}

$results = @()
$District = Resolve-District -CurrentDistrict $District -Url $BaseUrl

Invoke-JsonCheck -Name 'web_submit' -Method 'POST' -Url "$BaseUrl/api/submit" -Payload @{
    text = 'Layer1 smoke web submit'; language = 'en'; district = $District; source = 'Web'
} -AuthMode 'none' -WebhookToken $WebhookToken -AdminApiToken $AdminApiToken -Results ([ref]$results)

Invoke-JsonCheck -Name 'voice_submit' -Method 'POST' -Url "$BaseUrl/api/submit-voice" -Payload @{
    language = 'ta'; district = $District; source = 'Voice IVR'; audio_base64 = 'dGVzdA=='; audio_mime_type = 'audio/webm;codecs=opus'
} -AuthMode 'none' -WebhookToken $WebhookToken -AdminApiToken $AdminApiToken -Results ([ref]$results)

$challenge = 'layer1_challenge'
$waStatus = 0
$waPass = $false
$waText = ''
try {
    $waResp = Invoke-WebRequest -Uri "$BaseUrl/api/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=$WhatsAppVerifyToken&hub.challenge=$challenge" -Method Get -UseBasicParsing
    $waStatus = [int]$waResp.StatusCode
    $waText = [string]$waResp.Content
    $waPass = ($waStatus -eq 200 -and $waText -eq $challenge)
}
catch {
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
        $waStatus = [int]$_.Exception.Response.StatusCode
    }
}
Add-Result -Results ([ref]$results) -Name 'whatsapp_verify_get' -Http $waStatus -Pass $waPass -Note "challenge_echo=$waPass"

Invoke-JsonCheck -Name 'whatsapp_inbound_post' -Method 'POST' -Url "$BaseUrl/api/channels/whatsapp/webhook" -Payload @{
    message = 'Layer1 smoke whatsapp'; language = 'en'; district = $District; from = '919900000001'
} -AuthMode 'webhook' -WebhookToken $WebhookToken -AdminApiToken $AdminApiToken -Results ([ref]$results)

Invoke-JsonCheck -Name 'sms_keyword_new' -Method 'POST' -Url "$BaseUrl/api/channels/sms/keyword" -Payload @{
    text = 'NV NEW Layer1 smoke keyword'; from = '919900000002'; district = $District
} -AuthMode 'webhook' -WebhookToken $WebhookToken -AdminApiToken $AdminApiToken -Results ([ref]$results)

Invoke-JsonCheck -Name 'ivr_missed_call' -Method 'POST' -Url "$BaseUrl/api/channels/ivr/missed-call" -Payload @{
    phone = '919900000003'; district = $District; language = 'en'
} -AuthMode 'webhook' -WebhookToken $WebhookToken -AdminApiToken $AdminApiToken -Results ([ref]$results)

Invoke-JsonCheck -Name 'open_api_federation_publish' -Method 'POST' -Url "$BaseUrl/api/v1/federation/publish" -Payload @{
    source = 'layer1-smoke'; external_event_id = "smoke-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"; district = $District; text = 'Layer1 smoke federation'; language = 'en'; channel = 'Open API'
} -AuthMode 'admin' -WebhookToken $WebhookToken -AdminApiToken $AdminApiToken -Results ([ref]$results)

""
"{0,-34} {1,-8} {2,-6} {3}" -f 'CHECK', 'HTTP', 'RESULT', 'NOTE'
"{0,-34} {1,-8} {2,-6} {3}" -f ('-'*34), ('-'*8), ('-'*6), ('-'*28)
$results | ForEach-Object {
    "{0,-34} {1,-8} {2,-6} {3}" -f $_.Check, $_.Http, $_.Result, $_.Note
}
""
$passCount = ($results | Where-Object { $_.Result -eq 'PASS' }).Count
$totalCount = $results.Count
"Layer1 Smoke Summary: $passCount/$totalCount passed"

if ($passCount -ne $totalCount) {
    exit 1
}
