# 第十一课：商业 SaaS 原型——订阅套餐、模型路由、对账及部署验收

> 适用：已验收第十课的 **Windows 10/11 + PowerShell + Docker Desktop**。项目所在路径以你实际目录为准。本课是订阅、计量和对账的技术原型，**不会扣款、不会生成正式发票，也不保证覆盖未接入代理的 Langflow 调用**。

## 0. 第十课与第十一课差异

| 范围 | 第十课 | 第十一课 |
|---|---|---|
| 供应商 Chat Completions | 网关单轮 API | 增加 OpenAI-compatible `/v1` 模型代理，支持非流式文本及函数调用结构 |
| 账号、企业 | 企业管理员、成员 | 额外由平台运维分配订阅、冻结企业、签发独立企业模型密钥 |
| 额度 | 每日网关预算 | 保留每日预算，并按套餐进行**每自然月**内部成本上限及席位数量控制 |
| 结算 | 网关费用估算报表 | 分账期的**未核验账单草稿**、按请求 ID 对账的外部凭据导入 |
| 模型路由 | 单一固定模型 | 环境白名单别名 → 固定 HTTPS 供应商地址、模型、单价快照 |

### 故意没有提供的功能

- 不提供支付收款、银行卡、税务发票、订阅自动续期和真实 Stripe/支付宝接口。引入资金变更前必须设计签名 webhook、幂等结算、退款、税额及会计审计。
- 不计算仍直连供应商的旧 Langflow Agent、Embedding、异步索引、工具服务费用。只有完成**逐个工作流**改接并验证轨迹，才能宣称对应模型调用经由代理。
- 不保证所有第三方模型支持当前 OpenAI-compatible schema。网关目前只处理 `stream=false` 的文本及函数调用，拒绝图片和音频，多模态/Streaming 需要单独设计计量解析。
- `GATEWAY_ROUTES_JSON` 每条模型只能使用 **同一个固定的上游 HTTPS 域名与供应商 API Key**，不同供应商或多账号轮换需下一轮开发。
- 账单草稿中的“套餐费用”和“超出成本计算”是**演示定价**，不构成正式支付义务；外部导入 CSV 的来源真实性由运维人员独立核实。

## 1. 备份与校验文件

在普通 PowerShell 中：

```powershell
cd C:\Users\Administrator\Desktop\AI-Jump\smartbiz-agent-starter
$dc10 = @(
  '--env-file','.env','--env-file','.env.pg','--env-file','.env.ops',
  '--env-file','.env.gateway',
  '-f','docker-compose.yml','-f','docker-compose.pg.yml',
  '-f','docker-compose.ops.yml','-f','docker-compose.gateway.yml'
)
New-Item -ItemType Directory -Force .\lesson11-backup
# 在低流量维护期停止写入；Worker 正在生成的任务请先等待完成。
docker compose @dc10 stop smartbiz worker
# 数据库备份：完成前禁止迁移。
docker compose @dc10 exec -T postgres pg_dump -U smartbiz_owner -d smartbiz -Fc -f /tmp/before-0011.dump
docker compose @dc10 cp postgres:/tmp/before-0011.dump .\lesson11-backup\before-0011.dump
Get-Item .\lesson11-backup\before-0011.dump | Select-Object Name,Length
Copy-Item .\.env,.\.env.pg,.\.env.ops,.\.env.gateway .\lesson11-backup\
```

**在另一台/独立数据库中做一次 restore 演练**，并另行备份用户上传文件和 Langflow、Caddy 数据卷。请保护数据库快照及配置文件，不要上传至公共仓库。

## 2. 安装文件与环境配置

将 `smartbiz-lesson11-patch.zip` 下载到 Windows 下载目录，再执行：

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\smartbiz-lesson11-patch.zip" -DestinationPath (Get-Location) -Force
Copy-Item .env.commercial.example .env.commercial
notepad .env.commercial
```

`.env.commercial` 的例子只有一个别名 `tenant-default`，你可以按照下列 schema 加其它别名；**模型名及每百万 Token 三档单价必须按供应商适用价格重新核对**。

```dotenv
GATEWAY_ROUTES_JSON=[{"alias":"tenant-default","model":"gpt-4.1-mini","input":"0.40","cached":"0.10","output":"1.60"}]
```

这些价格只是演示快照，**不是 2026 年固定或未来适用的官方价格**。继续复用第十课 `.env.gateway` 中上游凭证。

## 3. 数据库升级（关键：先重构建 migrate 镜像）

```powershell
$dc11 = $dc10 + @('--env-file','.env.commercial','-f','docker-compose.commercial.yml')
docker compose @dc11 config --quiet
docker compose @dc11 build migrate
docker compose @dc11 run --rm migrate alembic heads
```

必须先看到 `0011_commercial (head)`，然后执行：

```powershell
docker compose @dc11 run --rm migrate alembic upgrade head
docker compose @dc11 run --rm migrate alembic current
```

预期 `0011_commercial (head)`。确认新增表：

```powershell
docker compose @dc11 exec -T postgres psql -U smartbiz_owner -d smartbiz -c "SELECT to_regclass('public.organization_subscriptions'),to_regclass('public.commercial_tenant_keys'),to_regclass('public.commercial_reconciliation_rows');"
```

若仍在 `0010_gateway_usage`，停下检查 `alembic heads` 和 migrate 镜像中脚本；**不要手工 CREATE TABLE、不要 `alembic stamp head`。**

```powershell
docker compose @dc11 up -d --build --force-recreate --no-deps smartbiz worker
docker compose @dc11 ps
```

## 4. 创建平台套餐 / 停用企业 / 企业专用模型密钥

本课刻意**没有公开超管 HTTP API**。以下命令使用已配置迁移 OWNER DSN 的 `migrate` 服务，不依赖终端暴露数据库密码，只有可信任的管理员能执行。

```powershell
# 列出最多 200 家企业及其套餐状态
docker compose @dc11 run --rm --no-deps migrate python -m app.commercial_operator --operator platform-owner list-orgs
```

复制某个 `organization_id`（它不是私密凭证）。修改套餐：

```powershell
$org = '这里填写企业 UUID'
docker compose @dc11 run --rm --no-deps migrate python -m app.commercial_operator --operator platform-owner assign-plan --org $org --plan team --status active
```

内置演示套餐：`starter`（5 个活跃账号，每月内部网关成本 2 USD）、`team`（25 个活跃账号，每月内部网关成本 20 USD）。月度窗口采用 **UTC 自然月**；全局日额度仍然生效，最终可用额度受两者共同限制。

企业暂停（会使其现有平台账号和模型专用密钥的后续认证失效）：

```powershell
docker compose @dc11 run --rm --no-deps migrate python -m app.commercial_operator --operator platform-owner assign-plan --org $org --plan team --status suspended
```

恢复：把 `--status suspended` 改为 `--status active`。注意冻结**不会撤回之前已发出的 API 响应，也不能撤销供应商已经处理的请求**。

生成专用模型密钥：

```powershell
docker compose @dc11 run --rm --no-deps migrate python -m app.commercial_operator --operator platform-owner create-key --org $org --label flow-A
```

**仅会输出一次** `key_id` 和 `sbm_...` 模型密钥。密钥不要截图公开、不要加入 GitHub 或与其他企业共用。可通过以下命令吊销：

```powershell
$keyId = '密钥ID'
docker compose @dc11 run --rm --no-deps migrate python -m app.commercial_operator --operator platform-owner revoke-key --org $org --key-id $keyId
```

## 5. 测试 OpenAI-compatible 模型代理

从服务器端或可信测试机用 PowerShell：

```powershell
$proxyKey = [System.Net.NetworkCredential]::new('',(Read-Host '输入企业专用模型密钥' -AsSecureString)).Password
$headers = @{Authorization="Bearer $proxyKey"}
Invoke-RestMethod 'https://localhost/v1/models' -Headers $headers
```

如果本地 Caddy CA 已按第九课安装并信任，以上 HTTPS 应正常工作；否则先排查证书，**不要为了图方便在正式调用中禁用证书验证**。

使用一个极短的非流式请求（将产生真实模型费用）：

```powershell
$body=@{
  model='tenant-default'
  stream=$false
  max_completion_tokens=80
  messages=@(@{role='user';content='用一句话解释 RAG'})
} | ConvertTo-Json -Depth 10
Invoke-RestMethod 'https://localhost/v1/chat/completions' -Method Post -Headers $headers -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

预期返回供应商原始的 Chat Completions 响应，其中含 `usage`。代理响应头带 `X-SmartBiz-Call-ID`（可用于追查账本）。如果要测试重复请求，请使用 `Idempotency-Key: UUID`，同一个 UUID 重复提交应返回 HTTP 409。

### 将 Langflow 中**某一个企业的专用工作流**改接代理

前提：Langflow 的 OpenAI-compatible provider 或自定义模型组件支持自定义 Base URL 和 API Key（参考官方文档）。在容器网络中使用：

- Base URL：`http://smartbiz:8000/v1`
- API Key：刚创建的 **该企业专用** `sbm_...` 密钥
- 模型：`tenant-default`
- Streaming：**关闭**；非流式响应支持文本与 function tool-calling schema，部分模型/客户端特性尚不支持。

验证代理日志/供应商 request ID、`model_gateway_calls` 是否产生新记录，以及 Agent Playground 工具轨迹是否依旧工作。

**严重边界：** 不可以在一个共享的多租户 Langflow Flow 上配置企业 A 的固定密钥却同时为企业 B 服务，那会把 B 的用量与信息归入 A。推荐为每企业建立隔离工作流/模型凭证，或另行实现一个由受信任后端按已认证请求动态下发租户令牌的网关适配层；目前本课尚未实现这种动态透传能力。使用 Langflow 自带的供应商模型节点、未改接流程、Embedding 调用均不纳入账本。

## 6. 管理工作台：套餐、路由与账单草稿

打开 `https://localhost/workspace`：

- 企业用户登录后，能查看本企业套餐、状态、模型别名与账号上限。
- 企业管理员可以选择账期查看**草稿**：套餐固定费用、网关内部用量估值、演示性的超出额度费用、差额数量；成员没有该权限。
- 草稿的 `payable_micro_usd = null`，不能直接当作真实发票；没有真实支付、退款和税务集成。
- 所有数据视图均使用经过认证的租户上下文，并受 PostgreSQL RLS 保护。前端在退出、账号切换、旧异步请求迟到时会清理私有内容。

### 后端关键路径

```
POST /v1/chat/completions
  → commercial_proxy.authenticate_service_key() [SHA256 哈希查企业，校验订阅]
  → configured_routes() [模型别名 -> 固定供应商模型与计费单价]
  → gateway_ledger_pg.reserve() [每日预算 + 套餐月度预算 + 幂等 ID]
  → model_gateway.provider_raw_chat() [保留 tool_calls, usage]
  → gateway_ledger_pg.succeed()/uncertain() [供应商报告 Token 用量 / 未知账目]
GET /api/v2/commercial/billing/draft
  → tenant_api.require_manager() [管理员]
  → commercial_pg.monthly_draft() [RLS 租户隔离]
```

## 7. 供应商外部凭据对账——仅由运维导入

只有在你从供应商系统**独立取得并确认来源**的明细中，存在逐请求的 `provider_request_id` 与费用，才能使用以下导入方案。常见供应商账单仅按项目/模型/时间窗口聚合，**并不保证提供逐请求费用**；这时不可编造 `provider_request_id`，需要另设计聚合口径对账。CSV 是**外部声明的费用**，脚本不负责验证文件来自供应商。

CSV UTF-8 示例（单位是 `micro_usd`，1 USD = 1,000,000 micro USD；不要填美元浮点数）：

```csv
provider_request_id,billed_micro_usd
chatcmpl-example-request-id,120
```

把该 CSV 保存到 `examples/provider_statement_11.csv`（此目录只放**脱敏测试凭据**），然后执行：

```powershell
docker compose @dc11 run --rm --no-deps -v "${PWD}/examples:/srv/app/recon-input:ro" migrate python -m app.commercial_operator --operator platform-owner reconcile-csv --file /srv/app/recon-input/provider_statement_11.csv --provider verified-provider
```

脚本按请求 ID 精确匹配网关请求，分成 `matched`、`difference`、`unmatched`、`ambiguous` 四种状态；**不匹配的行绝不按猜测的企业 ID 归属**，并保留批次 SHA-256 指纹、入库时间及操作日志。再次导入相同 CSV 会因 SHA 唯一约束而拒绝。

企业管理员的 `/api/v2/commercial/reconciliation` 只能看到**归属本企业的已匹配数据统计**。原始外部对账单和全平台不匹配项仅允许平台运维查看。

## 8. 自动化测试

原有单元测试：

```powershell
docker compose @dc11 run --rm --no-deps `
  -v "${PWD}:/srv/app" -w /srv/app `
  -e SMARTBIZ_TENANT_BACKEND=sqlite `
  -e GATEWAY_ENABLED=false `
  -e SMARTBIZ_DB_PATH=/tmp/smartbiz-lesson11-unit.sqlite3 `
  smartbiz python -m pytest -q tests
```

第十一课新增真实 PostgreSQL 隔离测试使用**完全独立、可丢弃的测试数据库容器**，不会覆盖当前 `smartbiz`：

```powershell
docker compose @dc11 --profile pgtest11 run --rm pgtest11
```

用例覆盖：创建两家企业、订阅初始化、RLS 禁止直接改套餐、运营端修改套餐、单企业密钥、供应商回执模拟、对账匹配、跨企业看不到对账、密钥吊销及订阅冻结。

此外请在浏览器中手工做 A→退出→B 的页面隔离测试，以及关闭/恢复套餐后的身份失效测试。**本代码生成环境没有 Docker/PG，真实 PG 用例未在此运行；必须以你的 Windows 结果为准。**

## 9. 故障定位

| 现象 | 优先检查 |
|---|---|
| `0010`/旧迁移版本 | `docker compose @dc11 build migrate`，再 `alembic heads/current` |
| `relation organization_subscriptions does not exist` | 0011 未实际升级；不要手动 CREATE TABLE 或 stamp |
| `/v1/models` 401 | 是否使用企业专用 `sbm_` 密钥、密钥是否吊销、套餐是否冻结 |
| `/v1/chat/completions` 422 | `model` 是否是受支持的别名、`stream` 是否关闭、是否包含不受支持的多模态字段 |
| 返回 402 | 套餐无效或达到 UTC 自然月内部网关额度 |
| 返回 429 | 第十课的每日网关预算已用尽 |
| Langflow 没有用量 | 该工作流很可能没有通过 `http://smartbiz:8000/v1`，或仍使用共享上游供应商 |
| Alembic 报权限错误 | 迁移只能用 smartbiz_owner，业务连接必须使用非超级用户 smartbiz_app |
| Worker 索引失败 | 与模型代理不同链路；检查原有 Embedding API、Worker 日志和队列指标 |

## 10. 回滚与交付边界

**安全回滚流程：** 先停止 Web 和 Worker 写入；备份第十一课运行期间产生的所有新增数据和独立服务器卷；在验证恢复后的独立环境，使用第十一课前备份恢复 PostgreSQL 数据和第十课代码/Compose 版本，再切换流量。这样会丢失备份之后的数据，必须按 RPO 由运维确认。不可直接运行 `down -v` 或 `alembic stamp`。也不建议数据库仍为 0011 时直接重建第十课的旧 migrate 镜像，因为那一版缺少迁移脚本。

**交付清单：** 域名证书与私有网络；租户 A/B 实际隔离；角色和套餐权限；限流/额度并发测试；密钥吊销；供应商真实 token usage；外部对账未匹配项处理；操作审计；Caddy/TLS；Prometheus/Alertmanager；任务恢复；数据库与文件备份恢复；浏览器 E2E；CI 构建；制品 digest；部署及回滚审批记录。

**产品上线阻断项：** 未接入代理的模型及 Embedding 无法完整计量；没有经供应商核验的账单/付款对账；无支付服务/税务合规；尚未做生产压力、渗透、隐私与数据保留评估。第十一课完成的是商用系统的可审计基础，不是已经可直接收款的正式 SaaS。
