# 第七课：修复跨账号残留 + PostgreSQL/pgvector/RLS/Alembic/审计

> **先读**：本课新增 opt-in PostgreSQL 租户后端。当前自动化验证包括 56 项通过和 1 项因当前环境无 PG 而跳过的真实数据库集成测试。只有在你本地 PostgreSQL、Windows Docker、浏览器实测之后才可确认端到端成功。此代码不适合无审计、安全加固的互联网生产部署。

## 0. 安全事故与根因（优先于数据库升级）

第六课 `web/workspace.js` 退出登录仅重置 `token`、`actor` 和 `hidden` 属性，未擦除 `documents`、`jobs`、`answer`、`sources`、问答文本和上传状态。在新用户登录前旧 DOM 已经存在，且迟到的 fetch response 仍可能覆盖新用户 UI。另有 /api/v2/ask 过去使用固定 `tenant:org:user:rag` Langflow `session_id`，在用户角色变更或资料回收后可能复用先前对话历史。这里均已修复。

- `clearPrivateUI()` 清理所有私有数据/表单，包括 `File` 输入。
- `resetIdentity()` 递增 `authEpoch` 并 `AbortController.abort()` 之前用户的所有请求。
- 每个 fetch 捕获本次身份及 epoch；回包后再次检查；过时响应立即作废。
- 登录切换前、退出时与 bfcache 页面恢复时均作清理；服务端对 `/workspace` 与所有 `/api/v2/*` 请求返回 `Cache-Control: no-store`。
- RAG 每次问答独立模型会话，避免以旧上下文回答当前权限下的问题；代价是该接口不自动保留跨轮上下文。

**上线前必须排查**：如果曾经在共享终端看到其他租户的真实材料，记录受影响用户/时间/数据范围，检查 HTTP 访问日志、Langflow traces、浏览器历史和数据保留政策；必要时按内部事故流程通知，不能仅刷新页面后宣称问题解决。删除模型侧历史必须按 Langflow 版本的数据管理操作进行，代码更新不会自动删除旧记录。

### 快速更新前端（无需迁移 PG）

先备份 `.env`、`web/workspace.js` 和数据库，然后覆盖补丁，执行：

```powershell
cd C:\AI\smartbiz-agent-starter
docker compose up -d --build --no-deps smartbiz
```

请在同一浏览器标签页依次登录企业 A、填写问题、完成问答、退出、登录 B；B 应看不到 A 的文件名、旧问题、回答、来源；再测试 A 请求未结束就退出，B 不应被迟到的 A 响应污染。

## 1. 第七课主要文件

| 文件 | 职责 |
|---|---|
| `web/workspace.js` | 安全清理、取消异步请求、阻断跨用户 DOM 写入 |
| `app/tenant_api.py` | 新增租户审计查询及独立的模型会话 ID |
| `app/tenant_backend.py` | SQLite/PG 后端显式切换 |
| `app/tenant_store_pg.py` | PostgreSQL 账号、租户文件、检索、任务、审计数据实现 |
| `alembic/versions/0007_pg_tenant_rls_audit.py` | Alembic DDL、pgvector/RLS/审计表及权限 |
| `docker-compose.pg.yml` | Postgres、Alembic 迁移服务和 pgtest 环境 |
| `app/pg_import.py` | 停机状态下单次 SQLite -> PG 导入，不迁移登录令牌 |
| `scripts/lesson07_rls_check.py` | 只读的无上下文拒绝及租户相互隔离检查 |
| `tests/test_lesson07_pg_live.py` | 可选真实 PG RLS/pgvector 访问测试（会写入测试数据） |

## 2. Windows 备份与前置检查

打开 Windows PowerShell（不是 Python REPL），确认 Docker Desktop 在 Linux containers 模式：

```powershell
cd C:\AI\smartbiz-agent-starter
docker compose ps
docker info --format '{{.OSType}}'
```

应该显示 `linux`。原有 worker 处理完任务，确认业务停机窗口。

**在线备份第六课 SQLite（不要仅复制 WAL 主文件）**：

```powershell
New-Item -ItemType Directory -Force .\lesson07-backup
docker compose exec -T smartbiz python -c "import sqlite3; s=sqlite3.connect('/data/smartbiz.sqlite3'); d=sqlite3.connect('/data/lesson07-sqlite-backup.sqlite3'); s.backup(d); d.close(); s.close(); print('backup ok')"
docker compose cp smartbiz:/data/lesson07-sqlite-backup.sqlite3 .\lesson07-backup\smartbiz.sqlite3
Copy-Item .\.env .\lesson07-backup\.env
```

备份文件内有客户资料与密钥；请设置访问权限，禁止上传公有仓库。

## 3. 安装增量源码

下载 `smartbiz-lesson07-patch.zip` 到 Windows 下载目录，然后：

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\smartbiz-lesson07-patch.zip" -DestinationPath "C:\AI\smartbiz-agent-starter" -Force
```

注意：补丁会覆盖 `web/workspace.js`、`app/tenant_api.py` 等第六课源码。如果你自己修改过，先对比 Git diff。补丁**不会覆盖 `.env` 或原 SQLite 文件**。

## 4. 创建 PostgreSQL 密钥文件（不放到旧 .env）

用三个不同的强随机十六进制密码，避免数据库 URL 转义问题：

```powershell
function New-Secret {
  $bytes = New-Object byte[] 24
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $rng.GetBytes($bytes)
  $rng.Dispose()
  return ([BitConverter]::ToString($bytes)).Replace('-', '')
}
@(
  "PG_OWNER_PASSWORD=$(New-Secret)"
  "PG_APP_PASSWORD=$(New-Secret)"
  "PG_WORKER_PASSWORD=$(New-Secret)"
) | Set-Content -Encoding ascii .env.pg
```

`.env.pg` 只供 Compose 插值；原始 `.env` 仍供现有服务运行，**不要把 PG_OWNER_PASSWORD 加进 `.env`**。`PG_APP_DSN` 只传给 SmartBiz，`PG_WORKER_DSN` 只传给 Worker，`PG_MIGRATION_DSN` 只传给一次性 Alembic 容器。生产环境应改用秘密管理器，并把旧 `.env` 中不属于 SmartBiz 的密钥也移出应用容器。

PowerShell 设置共用参数数组，后续都使用它：

```powershell
$dc = @('--env-file','.env','--env-file','.env.pg','-f','docker-compose.yml','-f','docker-compose.pg.yml')
docker compose @dc config --quiet
```

注意：不要执行并分享裸 `docker compose config`，其输出可能包含已插值密码。

## 5. 部署数据库并运行 Alembic

```powershell
docker compose @dc up -d postgres
docker compose @dc run --rm migrate
docker compose @dc logs --tail 50 postgres
```

`migrate` 运行 `alembic upgrade head`；迁移账号是数据库拥有者，日常 Web 应用使用不能绕过 RLS 的 `smartbiz_app`；Worker 使用独立账号。**pgvector 是 PostgreSQL 扩展，不是 Python Numpy 别名。**

验收 Alembic 的实际状态：

```powershell
docker compose @dc run --rm migrate alembic current
```

预期看到 revision `0007_pg_tenant (head)`。若报 `role ... does not exist`，说明初始化角色脚本未正确运行；只有在 **新建空 PG volume** 时它会自动执行，不能通过重新启动已初始化的数据库重跑。禁止随意删除现有数据卷。

## 6. 一次性导入第六课 SQLite 数据

> 请在维护窗口操作。切换不是在线双写迁移；切换期间旧 SQLite 和新 PostgreSQL 不会自动同步。

1. 等待旧任务完成，停止旧应用和 Worker，Langflow 可保持运行：

```powershell
docker compose stop smartbiz worker
```

2. 确保使用同一个 `smartbiz-data` 卷，先做只读检查：

```powershell
docker compose @dc run --rm --no-deps smartbiz python -m app.pg_import
```

3. 核对企业数、账号数、文档数、块数、任务数后，再正式导入：

```powershell
docker compose @dc run --rm --no-deps smartbiz python -m app.pg_import --execute
```

本脚本仅允许导入 **空的 PG 租户库**，所有行在一个 PostgreSQL 事务中提交；出错将回滚数据库写入。已有文档向量会以 pgvector 类型保存，源文件路径仍引用原共享数据卷。已有登录令牌不会迁移，请用户重新登录。审计新产生的 `migration.import` 事件。保留 SQLite 备份及旧卷。

4. 启动升级后的服务：

```powershell
docker compose @dc up -d --build smartbiz worker
docker compose @dc ps
docker compose @dc logs --tail 80 smartbiz
docker compose @dc logs --tail 80 worker
```

然后通过 `http://127.0.0.1:8000/workspace` 登录企业 A/B，检查文档、任务和重新发起检索；请使用演示数据验证权限。

## 7. RLS 原理与代码解释

- `app.tenant_id` 是由认证凭证取得的企业 ID，并在每一个 PostgreSQL 事务中用 `set_config(..., true)` 设置。
- `true` 表示 `SET LOCAL`：事务结束自动清除，避免连接被另一个企业复用时保留之前的上下文。
- `organization_documents`：仅本企业可见，同时检查 `visibility=organization` 或管理员角色。
- `organization_chunks` 和 `indexing_jobs`：除了租户 ID 还检查父文档访问许可。
- `audit_events`：管理员可看本企业事件；应用无 UPDATE/DELETE 审计日志表权限。
- RLS 使用 `FORCE ROW LEVEL SECURITY`，应用账号被检查为非超级用户和非 BYPASSRLS。独立 Worker 因需要跨企业领取任务使用特殊最小表权限和 BYPASSRLS，**绝不能向 Web 应用提供 Worker 数据库凭证**。

**重要边界**：`app.tenant_id` 是应用设置的自定义 GUC，拥有任意 SQL 执行能力的攻击者理论上可以重新设置它。该设计不能替代参数化 SQL、认证与访问控制，属于多层防御，生产环境需要继续审查数据库权限和 SQL 注入风险。并且 RLS 仅保护 PostgreSQL 表，不能保护浏览器旧 DOM、Langflow 内部日志和对话缓存。

pgvector 对 1536 维向量提供 HNSW 表达式索引，其他维度采用精确向量距离排序。近似索引在多租户过滤下可能降低召回，后续要做评测。

## 8. 验证 RLS、审核事件和前端缓存

**非破坏性检查**（需要两个已导入且各有测试文档的企业 UUID）：

```powershell
docker compose @dc exec smartbiz python -m scripts.lesson07_rls_check 企业A的组织ID 企业B的组织ID
```

输出应包含 `PASS: 无上下文默认拒绝...`。

**写入型 pgvector / RLS 集成测试**仅可在可丢弃的本地测试数据库执行。它会创建企业、文档、向量和审计记录；不要用于生产数据库：

```powershell
docker compose @dc --profile pgtest run --rm pgtest
```

原有的 SQLite 回归测试，可以在本机 Python 环境执行：

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
```

若 Windows 未安装 Node.js，前端 Node 测试会 `skip`，必须自己手动做浏览器多账号测试。如果已安装 Node.js，执行 `node tests/lesson07_frontend_test.cjs`。

在开发工作台 `GET /api/v2/audit` 中，只有企业管理员能查看该企业审计事件；普通成员返回 403，其他企业的事件不可见。审计内容包括时间、操作类型、操作者 ID、目标类型/ID、结果和非敏感摘要，不存文档正文、密钥或提示词。

## 9. 备份与回滚

### 9.1 PostgreSQL 逻辑备份

Windows PowerShell 5.1 可能损坏通过 `>` 重定向的二进制 `pg_dump`，因此不要这样操作。

推荐先在容器中生成文件再 `docker compose cp`：

```powershell
docker compose @dc exec postgres sh -c 'pg_dump -U smartbiz_owner -d smartbiz -Fc -f /tmp/smartbiz-pg.dump'
docker compose @dc cp postgres:/tmp/smartbiz-pg.dump .\lesson07-backup\smartbiz-pg.dump
```

最好再把数据库备份与文件上传卷的原始文档一起保存。仅备份 PostgreSQL 并不足以恢复上传文件。

### 9.2 应急切回第六课 SQLite

1. 停止新服务写入：`docker compose @dc stop smartbiz worker`。
2. 在原目录使用不带 pg override 的命令 `docker compose up -d --build smartbiz worker`，重新使用默认 `SMARTBIZ_TENANT_BACKEND=sqlite`（若 `.env` 曾改过，确保改回 sqlite）。
3. 原 SQLite 数据仍在原卷中，可以重新登录旧账号。
4. **重要**：PG 模式运行后产生的新账号、文档、任务和审计不会自动出现在旧 SQLite 中。切回属于回到迁移前的数据快照，需要先冻结写入、保留 PG 备份、制定人工差异回填方案，不可当作无损在线回滚。

### 9.3 Alembic schema downgrade（不要在正常回滚中执行）

`alembic downgrade base` 会删除新 PostgreSQL 业务表和审计表，**有破坏性**，仅允许在完整备份后的可丢弃开发数据库中使用。通常切回 SQLite 不需要执行这个命令，也不要使用 `docker compose down -v`。

## 10. 第七课学习任务

1. 解读 `clearPrivateUI()`，指出为什么隐藏 DOM 不等于数据被清空。
2. 解释 `authEpoch` 和 `AbortController`，理解回调与账户生命周期的竞争条件。
3. 解释 RLS `USING` 和 `WITH CHECK` 对 SELECT/INSERT/UPDATE 的差异。
4. 打开 `alembic/versions/0007_pg_tenant_rls_audit.py`，画出表关系，观察 Alembic 版本记录。
5. 比较 `tenant_store.py` 的 SQLite Python 点积搜索与 `tenant_store_pg.py` 的 pgvector SQL cosine 搜索。
6. 验证 Worker `FOR UPDATE SKIP LOCKED` 领取队列任务，并思考多 Worker 并发处理。
7. 验证企业 A/B 的资料、审计事件和可见范围不能互相查询。

## 11. 商用前缺口

MFA / SSO、正式授权与角色撤销策略、租户级密钥管理、审计防篡改外部存储、数据库 PITR、原文文件的安全扫描与加密、索引任务消息队列和死信机制、限流与资源配额、Langflow 管理平面租户隔离、对话历史与模型服务数据保留政策、自动化真实浏览器测试和第三方安全测试，均仍需要补齐。该项目截至第七课仍是 **可学习、可联调的 SaaS 原型**，不应作为已通过生产安全验收的产品直接对外出售。
