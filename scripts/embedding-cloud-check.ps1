# PowerShell 5.1+ / 7: test Embeddings provider from SmartBiz admin API.
# Does NOT reindex or delete data. Never prints secrets.
param(
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [string]$TestText = '售后工单响应时间与在线支持时段'
)
$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')
$secret = Read-Host '输入 ADMIN_API_KEY（不会显示）' -AsSecureString
$plain = [System.Net.NetworkCredential]::new('', $secret).Password
$client = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$loginBody = @{password = $plain} | ConvertTo-Json
try {
    $login = Invoke-RestMethod -Uri "$BaseUrl/api/admin/ui/login" -Method Post `
      -WebSession $client -ContentType 'application/json; charset=utf-8' `
      -Body ([System.Text.Encoding]::UTF8.GetBytes($loginBody))
} finally { $plain = $null; $loginBody = $null }
$headers = @{ 'X-CSRF-Token' = $login.csrf_token }
$before = Invoke-RestMethod -Uri "$BaseUrl/api/admin/kb/index" -WebSession $client
Write-Host "Current embedding spec: $($before.active_spec)"
Write-Host "Stored embedding specs: $($before.stored_specs -join ', ')"
Write-Host "Documents: $($before.document_count); chunks: $($before.chunk_count)"
Write-Host "Reindex needed: $($before.requires_reindex)"
$body = @{text = $TestText} | ConvertTo-Json
$probe = Invoke-RestMethod -Uri "$BaseUrl/api/admin/kb/embedding-test" -Method Post `
  -WebSession $client -Headers $headers -ContentType 'application/json; charset=utf-8' `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
Write-Host "Cloud mode: $($probe.cloud_mode); dimensions: $($probe.dimension); index modified: $($probe.index_modified)"
if (-not $probe.cloud_mode) { Write-Warning '当前仍是 demo 模式。请修改 .env 并重建 smartbiz 容器。' }
if ($before.requires_reindex) { Write-Warning '新旧模型不兼容。备份数据库后打开 /admin，点击“重新生成全部向量”。' }
$null = Invoke-RestMethod -Uri "$BaseUrl/api/admin/ui/logout" -Method Post -WebSession $client -Headers $headers
