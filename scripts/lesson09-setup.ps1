# Run in project root. Does NOT overwrite any existing operator secret.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$ops = Join-Path $root '.env.ops'
$secretFile = Join-Path $root 'ops\secrets\metrics_token'
New-Item -ItemType Directory -Force (Split-Path $secretFile) | Out-Null
if ((Test-Path $ops) -and (Test-Path $secretFile)) {
    Write-Host 'Existing .env.ops and scrape token preserved. No secrets displayed.'
    exit 0
}
if ((Test-Path $ops) -xor (Test-Path $secretFile)) {
    throw 'Only one of .env.ops / ops/secrets/metrics_token exists. Verify manually before continuing.'
}
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$secret = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
$opsText = @(
 'SMARTBIZ_DOMAIN=localhost',
 "SMARTBIZ_METRICS_TOKEN=$secret",
 'SMARTBIZ_DAILY_ESTIMATE_USD=2.00',
 'SMARTBIZ_RESERVE_PER_CALL_USD=0.02'
) -join "`n"
[IO.File]::WriteAllText($ops, $opsText + "`n", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($secretFile, $secret + "`n", [Text.UTF8Encoding]::new($false))
Write-Host 'Created .env.ops and ops/secrets/metrics_token. Restrict OS access and never commit them.'
