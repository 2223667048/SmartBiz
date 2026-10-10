# SmartBiz 第八课 — SaaS 安全与运维

适用于已完成第七课的项目。先备份 PostgreSQL 和 `.env`，再执行 Alembic 迁移。

完整中文教程：[`docs/11-第八课-租户安全-限流-审计-浏览器端到端.md`](docs/11-第八课-租户安全-限流-审计-浏览器端到端.md)

新增内容：租户持久化限流、用户停用与 Token 撤销、PostgreSQL Worker RLS 修复、索引任务监控、审计汇总、真实 Chromium E2E。

**不要直接对真实客户数据运行 `pgtest` 或浏览器 E2E。** `pgtest` 和浏览器 E2E 都会创建测试记录。本课不提供已经在 Windows Docker 环境验收完成的承诺；需要你在开发环境执行集成测试。

常用命令（PowerShell，在项目根目录）：

```powershell
$dc = @('--env-file','.env','--env-file','.env.pg','-f','docker-compose.yml','-f','docker-compose.pg.yml')
docker compose @dc stop smartbiz worker
docker compose @dc run --rm migrate
docker compose @dc run --rm migrate alembic current
docker compose @dc up -d --build --force-recreate --no-deps smartbiz worker
powershell -ExecutionPolicy Bypass -File .\scripts\lesson08-smoke.ps1
```

预期迁移版本：`0008_pg_ops (head)`。
