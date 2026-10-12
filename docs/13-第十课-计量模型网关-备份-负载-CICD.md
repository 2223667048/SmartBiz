# 第十课：真实 usage 计量网关、租户预算、恢复演练、负载测试与 CI/CD

## 0. 边界与验收

- **已覆盖**：通过 `/api/v2/model/chat`、`/api/v2/model/rag` 两个新端点调用的**单步、非流式** OpenAI 兼容 Chat Completions；使用供应商回传 `usage`；单位 `micro_usd`，1 美元 = 1,000,000 micro_usd；并且按租户保存明细。
- **未覆盖**：旧 `/api/v2/ask` 对 Langflow 的调用、Langflow Agent 内部多次推理、Embedding、工具调用、音视频、某些服务级收费。不要使用本报表向客户开具最终账单。
- **隐私**：账本只记录租户、用户标识、供应商请求 ID、模型、时间、tokens、单价快照、计费状态和美元计算结果；不保存原始问题及回答。
- **故障原则**：发生超时/供应商格式不完整时，标记 `uncertain` 并保留预算占用，防止故障被算作“零费用”。使用独立财务对账流程审核后才能释放预留金额（本课不提供自动释放）。
- **运维约束**：`.env.gateway` 包含真实供应商 API 密钥，不得上传到 git 或共享。`GATEWAY_ALLOWED_HOSTS` 必须限定受信任的供应商域名。

## 1. Windows 环境与备份

本教程适用于 Windows PowerShell、Docker Desktop、已通过第九课，目录例如：

```powershell
cd C:\Users\Administrator\Desktop\AI-Jump\smartbiz-agent-starter
$dc = @('--env-file','.env','--env-file','.env.pg','-f','docker-compose.yml','-f','docker-compose.pg.yml')
New-Item -ItemType Directory -Force .\lesson10-backup | Out-Null
Copy-Item .\docker-compose.pg.yml, .\app\main.py -Destination .\lesson10-backup\
Copy-Item .\.env, .\.env.pg -Destination .\lesson10-backup\
docker compose @dc stop smartbiz worker
docker compose @dc exec -T postgres pg_dump -U smartbiz_owner -d smartbiz -Fc -f /tmp/before-0010.dump
docker compose @dc cp postgres:/tmp/before-0010.dump .\lesson10-backup\before-0010.dump
Get-Item .\lesson10-backup\before-0010.dump | Select-Object Name,Length
```

请先检查数据库备份不是零字节；该备份仅覆盖 PostgreSQL，不包含 Docker `/data` 中的上传文件或 Langflow 工作流存储。后续完整 DR 还需这些卷的一致备份。

解压**第十课增量补丁**（先确认补丁实际保存在 Downloads）：

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\smartbiz-lesson10-patch.zip" -DestinationPath (Get-Location) -Force
```

## 2. 云端网关配置与数据库迁移

```powershell
Copy-Item .\.env.gateway.example .\.env.gateway
notepad .\.env.gateway
```

填入实际 `GATEWAY_PROVIDER_API_KEY`；确认 `GATEWAY_MODEL` 是供应商支持且允许的聊天模型，`GATEWAY_PROVIDER_BASE_URL` 是运营者控制的 HTTPS 地址，`GATEWAY_ALLOWED_HOSTS` 是匹配的主机。模型的输入/输出/缓存单价必须按供应商**当前对应计费档位**人工核对。示例价格只是教学值；注意供应商账单可能包括多模态、工具和服务费。

- `GATEWAY_DAILY_LIMIT_USD=2.00`：企业每天使用新网关的预估美元限额。
- `GATEWAY_CALL_HOLD_USD=0.10`：每次请求预留金额；此值不能保证严格限制真实费用（成本可能超过预留）。
- `GATEWAY_ENABLED=true`：只启用第十课网关；不会自动修改旧 Langflow Agent。

启动时加入 `gateway` 配置：

```powershell
$dc10 = @('--env-file','.env','--env-file','.env.pg','--env-file','.env.gateway',
          '-f','docker-compose.yml','-f','docker-compose.pg.yml','-f','docker-compose.gateway.yml')
# 首次检验 Compose 参数
 docker compose @dc10 config --quiet
# 每次加入迁移文件后都显式重建 migrate，不要只重建 smartbiz
 docker compose @dc10 build migrate
 docker compose @dc10 run --rm migrate alembic heads
# 预期：0010_gateway_usage (head)
 docker compose @dc10 run --rm migrate alembic upgrade head
 docker compose @dc10 run --rm migrate alembic current
# 预期：0010_gateway_usage (head)
 docker compose @dc10 up -d --build --force-recreate --no-deps smartbiz worker
 docker compose @dc10 ps
```

如需继续运行第九课 Caddy/Prometheus 等服务，将 `--env-file .env.ops -f docker-compose.ops.yml` 插入两组 Compose 文件后（gateway 文件放在最后），先执行 `config --quiet`，不要把敏感配置直接贴到在线聊天记录。

数据库表 `model_gateway_daily`、`model_gateway_calls` 强制 RLS。日额度更新使用 PostgreSQL 的 `INSERT ... ON CONFLICT ... DO UPDATE WHERE ... RETURNING` 原子扣留额度。数据库驱动使用非超级用户 `smartbiz_app`，不会自动赋予 `BYPASSRLS`。

## 3. API 测试：真实 Token 计量

登录企业工作台 `/workspace`，在“计量模型问答”输入小问题发送。它调用新的 `/api/v2/model/chat`。响应字段：

```json
{
  "answer":"...",
  "usage":{"prompt_tokens":100,"completion_tokens":30,"cached_prompt_tokens":0},
  "calculated_micro_usd":88,
  "currency":"USD",
  "model":"模型名",
  "call_id":"UUID",
  "cost_basis":"provider_reported_tokens_and_operator_prices",
  "not_provider_invoice":true
}
```

这个示例是假设输入每百万 Token $0.40、输出每百万 Token $1.60 时的计算结果，非真实响应。新的 `/api/v2/model/rag` 在服务端经过租户授权的 `scoped_search` 后，把来源发送到供应商；但问题向量化费用**不包含**在此网关报表内。旧 `/api/v2/ask` 不受影响。

管理员可以调用 `GET /api/v2/model/usage?days=7` 或通过工作台点击“刷新近 7 日用量”，查看每天成功请求的 Token 数、计算美元费用和未结算预留金额。普通成员可调用聊天接口，但没有报表读取权限。身份通过 Bearer token 在后端验证，客户端传入的 `organization_id` 被忽略。

幂等：每次调用需要 UUID `request_id`。用户重试同一个 UUID 会返回 `409`，不能在不知道先前调用是否成功时简单生成新 UUID 重试，否则可能造成重复计费。

供应商 HTTP 请求为**固定地址、非流式**；不支持客户端自定义 URL、任意 headers 或 tools。由于供应商网络故障的不确定性，发生 502 时不要自动重试。请对照供应商 Usage 报表人工核对。

## 4. 自动化测试

```powershell
# 从项目自带镜像执行回归
 docker compose @dc10 run --rm --no-deps -v "${PWD}:/srv/app" -w /srv/app smartbiz python -m pytest -q tests
```

真实 PG 集成测试使用本课新加的**独立 PostgreSQL 测试容器 `postgres-test10`**，数据库为 `smartbiz_test10`，不与生产数据卷共用；运行前再次检查 Compose 合并结果，避免错误覆盖测试数据库连接：

```powershell
 docker compose @dc10 --profile pgtest10 run --rm --build pgtest10
```

`migrate-test10` 会在单独的测试库执行到 `0010_gateway_usage`；`pgtest10` 向该测试库插入随机企业及账本记录。测试 DB 只有容器内部访问，没有主机端口，也没有命名数据库卷。

## 5. 备份与恢复演练

**仅开发环境**：新脚本进行 PostgreSQL custom-format 备份，再创建同一服务器上的 `smartbiz_restore_drill` **单独数据库**进行完整恢复；不会覆盖正在使用的 `smartbiz` 数据库。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\lesson10-backup-drill.ps1
```

脚本会检查 `alembic_version`、企业数、文档数及模型用量表。测试数据库为便于排错而保留，确认恢复正确后，可以手动删除 `smartbiz_restore_drill`（必须先确认连接的库名）。**同机恢复不等于异机容灾**；上线前还需在第二台机器验证文件、数据卷和证书的恢复。

回滚准则：先阻止新写入并导出备份。可以切回第九课代码，但**新增模型网关的账本数据不会被第九课界面识别**。不应该直接 `alembic downgrade 0009_pg_budget`，因为第十课两张账本表会被删除。数据库如果要恢复，应在隔离环境先使用备份演练，确认恢复窗口和数据丢失范围。

## 6. k6 低风险性能基线

```powershell
$load = $dc10 + @('-f','docker-compose.load.yml')
 docker compose @load --profile loadtest run --rm loadtest
```

测试 5 个 VU，20 秒，仅请求 `/health`，不会触发付费模型调用。不要将 health 压测结果当作 RAG 或 LLM 承载能力；真实业务压力测试需要使用 mock 模型、隔离数据库、明确峰值和使用配额。

## 7. CI/CD

- `.github/workflows/ci.yml`：每次 push/PR 运行单元回归、代码编译及 Docker Build（不连接真实模型）。
- `.github/workflows/release.yml`：只有手动触发或发布版本标签才构建、推送 GHCR 镜像；不通过聊天机器人自动部署生产服务。生产部署需要审批、数据库备份与迁移、镜像 digest 固定、烟雾测试及回滚计划。
- CI 不应持有真实企业数据或模型密钥。对外发布前审查 GitHub Actions 第三方 action 的固定 SHA、最小权限、依赖扫描与镜像签名。

## 8. 源码阅读与常见错误

- `app/model_gateway.py`：云端供应商协议、校验 `usage`、单价计算。不要把输入长度当作 Token 数。
- `app/gateway_ledger_pg.py`：原子预留、幂等键、成功后记账、未知状态处理。
- `app/model_gateway_api.py`：企业认证、API、只读租户报表、检索后直接模型调用。
- `alembic/versions/0010_model_gateway_usage.py`：版本化模型账本数据库迁移与 RLS。
- `scripts/lesson10-backup-drill.ps1`：备份恢复测试与边界。
- `ops/load/smoke.js`：不触发计费 API 的 k6 健康负载。

报错排查：

| 现象 | 优先检查 |
|---|---|
| 503 网关关闭 | `GATEWAY_ENABLED`、.env.gateway、模型名称和单价 |
| 429 配额 | `GATEWAY_DAILY_LIMIT_USD`、`GATEWAY_CALL_HOLD_USD`、当天 used+held |
| 409 | 重复的 request_id；不要直接换 UUID 再盲目重试 |
| 502 且账本 uncertain | 上游请求失败或缺 usage；对账后再处理预留金额 |
| 500 UndefinedTable | `migrate` 镜像未重建或数据库未升级到 0010 |
| HTTPS 证书 | 本机 Caddy 根 CA 信任；第九课已处理 |
| 实际供应商费用不一致 | 单价模型、套餐、工具费用、Langflow 和 Embedding 不在报表范围内 |
