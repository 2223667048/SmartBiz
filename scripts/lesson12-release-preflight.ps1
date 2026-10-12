# A read-only operational preflight. This does NOT approve deployment.
param([Parameter(Mandatory=$true)][string]$Org, [Parameter(Mandatory=$true)][string]$Period,
      [string]$Inventory='examples/flow-inventory.example.json')
$ErrorActionPreference='Stop'
$files=@('docker-compose.yml','docker-compose.pg.yml','docker-compose.ops.yml',
  'docker-compose.gateway.yml','docker-compose.commercial.yml','docker-compose.langflow-safe.yml')
foreach($file in $files) { if(-not (Test-Path $file)) { throw "Missing $file" } }
$envFiles=@('.env','.env.pg','.env.ops','.env.gateway','.env.commercial')
foreach($file in $envFiles) { if(-not (Test-Path $file)) { throw "Missing $file" } }
if(-not (Test-Path $Inventory)) { throw "Missing flow inventory: $Inventory" }
$dc=@('--env-file','.env','--env-file','.env.pg','--env-file','.env.ops',
  '--env-file','.env.gateway','--env-file','.env.commercial',
  '-f','docker-compose.yml','-f','docker-compose.pg.yml','-f','docker-compose.ops.yml',
  '-f','docker-compose.gateway.yml','-f','docker-compose.commercial.yml')
docker compose @dc config --quiet
if($LASTEXITCODE -ne 0) { throw 'Compose config invalid' }
docker compose @dc run --rm migrate alembic current
if($LASTEXITCODE -ne 0) { throw 'Cannot verify DB migration' }
Write-Host 'Expected Alembic: 0011_commercial (unless you installed a newer migration)'
Write-Host 'Checking unapproved flows and provider matches (never claims coverage of unobserved calls)'
docker compose @dc run --rm --no-deps -v "${PWD}:/srv/app" -w /srv/app migrate `
  python -m app.delivery_operator --org $Org --period $Period --inventory $Inventory
if($LASTEXITCODE -eq 2) { Write-Warning 'Readiness blockers found; deployment must not be promoted.' }
elseif($LASTEXITCODE -ne 0) { throw 'Delivery report failed' }
Write-Host 'Complete independent DB restore, Langflow backup and browser isolation checks separately.'
