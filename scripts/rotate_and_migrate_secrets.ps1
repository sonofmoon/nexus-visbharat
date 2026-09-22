# Rotate exposed credentials and migrate Cloud Run to Secret Manager
$ErrorActionPreference = 'Stop'

$ProjectId = 'nexus-visbharat'
$Region = 'asia-south1'
$Service = 'nexus-visbharath'
$Instance = 'nvb-postgres'

# 1. Generate new credentials
Add-Type -AssemblyName System.Security
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$NewDbPassword = [System.Convert]::ToBase64String($bytes) -replace '[/+=]', 'x'

$bytes2 = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes2)
$NewWebhookSecret = [System.Convert]::ToBase64String($bytes2) -replace '[/+=]', 'w'

$AdminToken = 'nvb-adm-' + ([System.Guid]::NewGuid().ToString('N'))
$AnalystToken = 'nvb-ana-' + ([System.Guid]::NewGuid().ToString('N'))
$AuditorToken = 'nvb-aud-' + ([System.Guid]::NewGuid().ToString('N'))

Write-Host "Generated new secure credentials."

# 2. Update Cloud SQL password
Write-Host "Updating Cloud SQL user password..."
gcloud sql users set-password visbharat_user --instance=$Instance --project=$ProjectId --password=$NewDbPassword --quiet
Write-Host "Cloud SQL password updated."

$DatabaseUrl = "postgresql://visbharat_user:$NewDbPassword@/visbharat?host=/cloudsql/$ProjectId`:$Region`:$Instance"
$GoogleAiKey = $env:GOOGLE_AI_API_KEY
$GoogleMapsKey = $env:GOOGLE_MAPS_API_KEY
$TelegramBotToken = $env:TELEGRAM_BOT_TOKEN

$secrets = @{
    'nvb-database-url' = $DatabaseUrl
    'nvb-google-ai-api-key' = $GoogleAiKey
    'nvb-google-maps-api-key' = $GoogleMapsKey
    'nvb-telegram-bot-token' = $TelegramBotToken
    'nvb-telegram-webhook-secret' = $NewWebhookSecret
    'nvb-admin-api-token' = $AdminToken
    'nvb-analyst-api-token' = $AnalystToken
    'nvb-auditor-api-token' = $AuditorToken
}

foreach ($name in $secrets.Keys) {
    $val = $secrets[$name]
    $check = gcloud secrets describe $name --project=$ProjectId 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Creating secret $name..."
        gcloud secrets create $name --replication-policy=automatic --project=$ProjectId --quiet
    }
    Write-Host "Adding version to $name..."
    $val | gcloud secrets versions add $name --data-file=- --project=$ProjectId --quiet
}

# 3. Update local .env
$envContent = @"

ADMIN_API_TOKEN=$AdminToken
ANALYST_API_TOKEN=$AnalystToken
AUDITOR_API_TOKEN=$AuditorToken
TELEGRAM_WEBHOOK_SECRET=$NewWebhookSecret
"@
Add-Content -Path .env -Value $envContent
Write-Host "Updated local .env."

Write-Host "All secrets stored in Secret Manager."
