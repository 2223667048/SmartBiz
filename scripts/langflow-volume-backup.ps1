param([Parameter(Mandatory=$true)][string]$VolumeName)
# Stop Langflow BEFORE using this snapshot to prevent inconsistent SQLite WAL backups.
$ErrorActionPreference='Stop'
if ($VolumeName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]*$') { throw 'Invalid volume name' }
$exists = @(docker volume ls --format '{{.Name}}' | Where-Object { $_ -eq $VolumeName })
if ($exists.Count -ne 1) { throw 'Volume not found. Nothing copied.' }
$destination = Join-Path (Get-Location).Path 'langflow-backups'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$filename = ('langflow-' + $stamp + '.tar.gz')
# The container mounts the source volume read-only; no deletion or restoration here.
docker run --rm `
  --mount "type=volume,source=$VolumeName,target=/source,readonly" `
  --mount "type=bind,source=$destination,target=/backup" `
  alpine:3.20 sh -c "cd /source && tar -czf /backup/$filename ."
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
$path=Join-Path $destination $filename
Get-Item $path | Select-Object FullName, Length
Write-Host 'Sensitive backup: encrypt and store securely. Confirm archive before modifying mounts.'
