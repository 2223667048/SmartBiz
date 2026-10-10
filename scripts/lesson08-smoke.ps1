param([string]$BaseUrl='http://127.0.0.1:8000')
$ErrorActionPreference = 'Stop'
Write-Host 'SmartBiz lesson 08: PostgreSQL /api/v2 smoke test (no secrets are printed)'
$email = Read-Host '管理员邮箱'
$secure = Read-Host '管理员密码' -AsSecureString
$password = [System.Net.NetworkCredential]::new('', $secure).Password
$body = @{email=$email; password=$password} | ConvertTo-Json
$encoded = [Text.Encoding]::UTF8.GetBytes($body)
try {
  $login = Invoke-RestMethod -Uri "$BaseUrl/api/v2/auth/login" -Method Post -Body $encoded -ContentType 'application/json; charset=utf-8'
} finally { $password = $null; $body = $null; $encoded = $null }
$auth = @{Authorization = "Bearer $($login.access_token)"}
try {
  $user = Invoke-RestMethod -Uri "$BaseUrl/api/v2/auth/me" -Headers $auth
  Write-Host "User: $($user.email); role: $($user.role)"
  $metrics = Invoke-RestMethod -Uri "$BaseUrl/api/v2/jobs/metrics" -Headers $auth
  Write-Host 'Index task metrics:'
  $metrics | ConvertTo-Json -Depth 5
  $audit = Invoke-RestMethod -Uri "$BaseUrl/api/v2/audit/summary" -Headers $auth
  Write-Host 'Audit count groups (past 24h):'
  $audit | ConvertTo-Json -Depth 5
} finally {
  try { Invoke-RestMethod -Uri "$BaseUrl/api/v2/auth/logout" -Method Post -Headers $auth | Out-Null } catch {}
  $auth = $null
}
Write-Host 'Smoke test completed.'
