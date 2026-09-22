# Prepare a no-traffic Cloud Run revision using existing, approved PostgreSQL resources.
# No database is provisioned, no old records are migrated, and no traffic is promoted.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Image,
    [Parameter(Mandatory=$true)][string]$CloudSqlInstance,
    [Parameter(Mandatory=$true)][string]$DatabaseUrlSecret,
    [Parameter(Mandatory=$true)][string]$ServiceAccount,
    [string]$ProjectId = 'nexus-visbharat',
    [string]$Region = 'asia-south1',
    [string]$Service = 'nexus-visbharath',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
if ($Image -notmatch '@sha256:[a-f0-9]{64}$') { throw 'Supply an immutable image digest, not a mutable tag.' }
if ($CloudSqlInstance -notmatch '^[a-z0-9-]+:asia-south[12]:[a-z0-9-]+$') { throw 'Supply an India-region Cloud SQL connection name: project:region:instance.' }
if ($DatabaseUrlSecret -notmatch '^[a-zA-Z0-9_-]+:[0-9]+$') { throw 'Pin the existing database URL secret to a numeric version: secret-name:version.' }
$deploymentArgs = @('run','deploy',$Service,'--project',$ProjectId,'--region',$Region,
    '--image',$Image,'--service-account',$ServiceAccount,'--add-cloudsql-instances',$CloudSqlInstance,
    '--update-secrets',('DATABASE_URL='+$DatabaseUrlSecret),
    '--update-env-vars','AUTO_MIGRATE=false,SEED_DEMO_DATA=false,ALLOW_EPHEMERAL_SHOWCASE=false,LOCAL_EVALUATION_WORKER=false',
    '--concurrency','8','--max-instances','2','--no-traffic','--tag','submission-review',
    '--startup-probe','httpGet.path=/readyz,initialDelaySeconds=0,timeoutSeconds=3,periodSeconds=10,failureThreshold=12',
    '--quiet')
Write-Output 'Prerequisites: approved Cloud SQL instance, schema/data migration, SQL-client and secret-access permissions for the existing service identity, and a database backup.'
Write-Output 'Database secret must contain a PostgreSQL URL using host=/cloudsql/PROJECT:REGION:INSTANCE. Never put its value on a command line.'
Write-Output 'Planned command arguments:'
$deploymentArgs | ConvertTo-Json
if (-not $Apply) { Write-Output 'Plan only. After review and migration, run with -Apply. Existing traffic remains unchanged.'; return }
& gcloud @deploymentArgs
if ($LASTEXITCODE -ne 0) { throw 'Revision deployment failed; existing traffic has not been promoted.' }
Write-Output 'Verify the submission-review revision, restart persistence, two-instance consistency and the five submission requirements before any traffic promotion.'
