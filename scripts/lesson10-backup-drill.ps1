# Run ONLY on a dedicated development PostgreSQL instance.
# Keeps backup on host and tests restore into a separate DB in the SAME cluster.
# Requires .env.pg with smartbiz_owner credentials already loaded by Compose.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$dc = @('--env-file','.env','--env-file','.env.pg','-f','docker-compose.yml','-f','docker-compose.pg.yml')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Force '.\backups' | Out-Null
$hostPath = ".\backups\smartbiz-$stamp.backup"
$drillDb = 'smartbiz_restore_drill'
# Stop writes for a clean application-level drill. pg_dump itself gives a consistent DB snapshot,
# but database dumps do not contain /data/tenant-uploads or Langflow volumes.
Write-Host '1) Backing up database...'
& docker compose @dc exec -T postgres pg_dump -U smartbiz_owner -d smartbiz -Fc -f /tmp/lesson10-drill.dump
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
& docker compose @dc cp 'postgres:/tmp/lesson10-drill.dump' $hostPath
if ($LASTEXITCODE -ne 0) { throw 'copy failed' }
if ((Get-Item $hostPath).Length -lt 1000) { throw 'dump unexpectedly small' }
Write-Host "Backup saved: $hostPath"
Write-Host '2) Checking restore DB does not already exist...'
$exists = & docker compose @dc exec -T postgres psql -U smartbiz_owner -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$drillDb'"
if ($LASTEXITCODE -ne 0 -or $exists -eq '1') { throw 'Drill database exists or check failed. Refusing to touch it.' }
Write-Host '3) Creating and restoring into isolated DB (not production DB)...'
& docker compose @dc exec -T postgres createdb -U smartbiz_owner $drillDb
if ($LASTEXITCODE -ne 0) { throw 'createdb failed' }
& docker compose @dc exec -T postgres pg_restore --exit-on-error --no-owner -U smartbiz_owner -d $drillDb /tmp/lesson10-drill.dump
if ($LASTEXITCODE -ne 0) { throw 'restore failed. Inspect the drill database; original is intact.' }
Write-Host '4) Verify restored schema and tenant counts...'
& docker compose @dc exec -T postgres psql -U smartbiz_owner -d $drillDb -c 'SELECT version_num FROM alembic_version; SELECT COUNT(*) AS organizations FROM organizations; SELECT COUNT(*) AS documents FROM organization_documents; SELECT COUNT(*) AS usage_rows FROM model_gateway_daily;'
if ($LASTEXITCODE -ne 0) { throw 'restore verification failed' }
Write-Host 'Restore drill completed. Keep backup; database smartbiz_restore_drill remains for manual inspection.'
