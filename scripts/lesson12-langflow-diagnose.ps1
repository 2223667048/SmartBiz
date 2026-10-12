# Safe, read-only. Never removes containers/volumes. Windows PowerShell 5.1+.
$ErrorActionPreference = 'Stop'
Write-Host '[1] Langflow-related volumes (DO NOT DELETE)'
docker volume ls --format '{{.Name}}' | Select-String 'langflow'
Write-Host '[2] All Langflow containers including stopped'
docker ps -a --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}' | Select-String 'langflow'
Write-Host '[3] Current Compose project and live volume paths'
docker compose ls --all
$containers = @(docker ps -a --format '{{.ID}} {{.Names}}' | Where-Object { $_ -match 'langflow' })
foreach ($c in $containers) {
  $id = ($c -split '\s+')[0]
  Write-Host "Container: $id"
  docker inspect $id --format 'Image={{.Config.Image}}; Mounts={{json .Mounts}}'
}
Write-Host '[4] Inspect each candidate volume with a READ-ONLY helper container'
$volumes = @(docker volume ls --format '{{.Name}}' | Where-Object { $_ -match 'langflow' })
foreach ($vol in $volumes) {
  if ($vol -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]*$') { throw "unexpected volume name $vol" }
  Write-Host "Volume: $vol"
  docker run --rm --mount "type=volume,source=$vol,target=/source,readonly" alpine:3.20 `
    sh -c 'find /source -maxdepth 7 -type f \( -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" -o -name "*.json" \) -exec ls -lh {} \; | head -80'
}
Write-Host 'Never run docker compose down -v or docker volume prune during recovery.'
