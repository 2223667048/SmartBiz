# 第十二课：Langflow 恢复、交付门禁、费用覆盖率、发布与客户验收

## 0. 先保护现场

第 11 课原始 Compose：`langflow-data:/app/langflow`、`LANGFLOW_CONFIG_DIR=/app/langflow`，但未设置 `LANGFLOW_SAVE_DB_IN_CONFIG_DIR=True` 或 `LANGFLOW_DATABASE_URL`，镜像使用 `:latest`。**不保证应用数据库实际落在卷内**。另外，改变 Compose 项目名也会让 Docker 连接到新卷。SSRF allowlist 本身不删除流程。

1. **不要**执行 `down -v`、`docker volume prune`、`docker system prune --volumes`、删除旧容器或覆盖旧数据库。
2. 执行 `powershell -ExecutionPolicy Bypass -File .\scripts\lesson12-langflow-diagnose.ps1`；保存输出（脱敏后）用于判断。
3. 用 `docker compose ls --all` 和 `docker volume ls` 判断是否发生项目名/卷名变化。用 `docker inspect <CONTAINER> --format '{{json .Mounts}}'` 查看当前卷；检查旧容器 `docker ps -a`。旧 SQLite 数据有时仅在已停止的**旧容器层**，此时必须优先 `docker cp <OLD_CONTAINER>:/path/to/langflow.db ...`，不要先删容器。
4. **先停止相关 Langflow 容器**。对每一个候选旧卷执行 `powershell -ExecutionPolicy Bypass -File .\scripts\lesson12-langflow-volume-backup.ps1 -VolumeName 实际卷名`。对压缩包核对体积、校验 hash 并隔离保存。
5. 找到旧 DB 之后**复制一份**到独立目录，用 sqlite3 或 Python `sqlite3` 只读查看 `sqlite_master` 中是否有 `flow` 表、`SELECT COUNT(*) FROM flow`，然后决定恢复哪个实例。**禁止把发现的旧 DB 直接写入当前正在运行的卷。** 还要一并保留原 config/secret_key 和上传文件；否则可能无法解密先前保存的密钥。
6. 如果旧卷/旧容器/旧备份均无数据库，不能承诺恢复。改按下文流程重建并导出 JSON。

## 1. 修复持久化（仅在数据已备份且目标卷确认无误后）

原 Compose 项目名要保持不变。在 `.env.langflow-safe` 中填写**当前已验证的镜像版本**（可用 `docker inspect <container> --format '{{.Config.Image}}'`）。新的 `docker-compose.langflow-safe.yml` 将 SQLite 显式设置为 `sqlite:////app/langflow/langflow.db`，并保持对 `/app/langflow` 的原有命名卷挂载。

**重要：这条路径会连接一个新数据库，除非你已经确认老数据的数据库文件也在该位置！** 如果旧数据库文件在容器其他目录，应先备份、停机、离线迁移数据库与密钥、检查兼容性，再启用覆盖文件。不要在不核对数据库的情况下直接启动。

SSRF 安全配置默认启用，仅将 `smartbiz` 纳入允许地址：

```dotenv
LANGFLOW_SSRF_PROTECTION_ENABLED=true
LANGFLOW_CONNECTOR_SSRF_VALIDATION_ENABLED=true
LANGFLOW_CONNECTOR_SSRF_ALLOW_LOOPBACK=false
LANGFLOW_SSRF_ALLOWED_HOSTS=smartbiz
```

如果你有更严格的威胁模型，不应该让不受信任的客户拥有可任意编辑 Python 自定义组件的 Langflow 管理权限；allowlist 本身不能限制任意 Python 网络访问。

## 2. 重建所有课程 Langflow 工作流

### A. 基础问答 Agent（第一、三课）

1. 登录 `http://localhost:7860` → New Flow → Simple Agent，或使用 Chat Input、Agent、Chat Output 组件连接。
2. 模型 Provider 选择**受支持且具备 Tool Calling 功能**的模型，在 Playground 测试「你好」。
3. Agent Instructions：专业中文、不可编造报价、非授权信息不回答、工具结果为演示时明确说明。
4. 同一 Langflow 会话发送「测试代号蓝鲸7412」和「我的代号是什么？」；新 session ID 不应恢复旧会话记忆。
5. **先保存工作流，再获取 Flow ID**。Flow 编辑页 Share → API access，记录 UUID。
6. 在 Langflow Settings → API Keys 生成**新的** Langflow API Key（不要把企业模型密钥误用为 Langflow API Key）。
7. 编辑 SmartBiz 根目录 `.env`，设置 `AGENT_MODE=langflow`、`LANGFLOW_FLOW_ID=<新 UUID>`、`LANGFLOW_API_KEY=<新 API Key>`。容器内 `LANGFLOW_URL` 为 `http://langflow:7860`，不要改成容器内部的 localhost。
8. `docker compose ... up -d --force-recreate --no-deps smartbiz`，通过 `/workspace` 或已启用的 v2 问答业务功能验证；单企业历史 `/api/chat` 在多租户模式下已经被封禁。

### B. 企业服务目录工具（第三课，历史演示）

自定义组件 `extensions/smartbiz_catalog_tool.py` → Tool Mode → Agent Tools。第三课的 `http://smartbiz:8000/api/catalog/search` 属于**演示接口**，多租户模式中不得作为有权限约束的真实业务数据源。仅在隔离实验环境复现，不用于正式企业工作流。

### C. 企业知识库工具（第四课，历史演示）

组件 `extensions/smartbiz_knowledge_tool.py` 访问旧 `/api/kb/search` 并使用 `KB_SERVICE_KEY`。第六课起 `SMARTBIZ_MULTI_TENANT=true` 后旧 `/api/kb/*` 不再开放。**不能通过重新启用这些共享密钥旧接口解决问题**，否则可能绕过企业文档权限。

正式 RAG 使用现有 `POST /api/v2/model/rag`（前端已认证企业用户，网关直接提供答案与来源）或重新实现**受租户认证的 Agent 工具**。当前项目尚未实现安全地从 Langflow 流程传递任意用户的身份给知识检索工具，不应宣称它已经完成。

### D. Langflow 企业独立模型路由（第十一课）

1. 平台运维为指定企业创建一次性 `sbm_...` 代理密钥：`python -m app.commercial_operator --operator ... create-key --org ... --label ...`。
2. 在该企业的**独立** Langflow Flow 模型提供商中设置 Base URL `http://smartbiz:8000/v1`；API Key 为该企业自己的 `sbm_...`，模型别名为 `tenant-default`；关闭 streaming。
3. 检查模型组件能调用 `GET /v1/models` 和 `POST /v1/chat/completions`，查看真实执行轨迹，并在该企业 `model_gateway_calls` 中看到成功记录。
4. **一个共享 Flow 不得固定使用企业 A 的 sbm 密钥同时服务 A、B**。当前设计必须按租户分离 Flow/Key，或在后续实现具备完整租户绑定的动态模型代理。
5. 重建或重新导入后，Flow ID 可能变化。逐一登记 Flow 名称、ID、所属企业、模型路由、执行验收日期；更新 SmartBiz `.env` 中的 `LANGFLOW_FLOW_ID`，重启 SmartBiz；不能只改 Langflow UI。
6. 每个 Flow 导出 JSON（编辑器 Share → Export，或项目导出）；**不要勾选导出 API Key**。将备份加密存档，并独立记录变量名、模型别名与工具参数。

### Flow ID、API Key 等三种凭证

| 凭证 | 存放位置 | 作用 |
|---|---|---|
| `LANGFLOW_FLOW_ID` | SmartBiz `.env` | 选中哪个 Langflow 工作流 |
| `LANGFLOW_API_KEY` | SmartBiz `.env` | SmartBiz 向 Langflow 认证 |
| `sbm_...` | 单独的企业 Flow 凭据 | Langflow 经 SmartBiz /v1 调用模型并记账 |

## 3. 交付验收范围与费用覆盖率

执行：

```powershell
# 将 examples/flow-inventory.example.json 复制并填写真实 flow_id、routing、verified_at。
# 切记：该文件只是运营人员自查清单，不是自动发现 Langflow 所有调用。
docker compose @dc11 run --rm --no-deps `
  -v "${PWD}:/srv/app" -w /srv/app migrate `
  python -m app.delivery_operator --org $org --period 2026-10 `
  --inventory examples/flow-inventory.example.json
```

该报告仅统计 `model_gateway_calls`（按当前月和企业）以及账本中唯一匹配的 reconciliation。任何 `legacy_direct`、`not_tested` Flow 阻止将计量覆盖率称为已核实。这个核查只能表明清单中由运营人员确认的工作流采用网关；**不能证明没有遗漏的影子工作流和其他模型账号**。

供应商费用对账必须通过可核验来源获取、保存账期、货币、账号和文件 SHA256，执行 `reconcile-csv`，审阅 `unmatched / ambiguous / difference / uncertain` 后人工批准；不要自动归属不明的请求，也不能将 `payable_micro_usd = null` 改成已收款。

## 4. 支付系统设计（本课只有状态机，未收款）

新代码 `app/delivery_control.py` 的 `PAYMENT_STATES` 是**纯状态转移校验器**，不执行资金操作。商业接入仍需：选择合规支付服务商；凭证托管；Webhook 签名验证；事件幂等键和唯一约束；金额与货币核对；异步支付确认；退款与拒付；税务发票；账务总账和审计留存。尤其不能根据浏览器跳转成功页直接判断支付成功。

状态序列：`draft → approved → invoice_issued → payment_pending → paid`，退款 `paid → refund_pending → refunded`。没有接入支付服务商前不能提供真实支付按钮。

## 5. 灰度发布与回滚

建议顺序：备份 SmartBiz PG、Langflow DB/Flow JSON/secret_key、上传文件与 Caddy 配置 → 恢复演练 → 迁移检查 → 5% 内部试用用户 → 25% → 100%，每一阶段检查 HTTP 5xx、p95、RAG 质量、费用差异、任务队列、数据隔离、密钥漏出情况。

当前 Compose 没有实现真正的流量切分或自动路由，百分比属于**待部署网关或功能标志支持的发布策略**，不应声称已经完成流量灰度。灰度失败时优先停止新流量、回退镜像；Alembic 执行过的破坏性迁移不能假设可自动降级，必须提前采用向后兼容 expand/contract 变更。数据库恢复是独立灾难恢复流程，不等于版本回滚。

## 6. 跨租户安全与恢复演练

- 在隔离测试数据库执行已有 `pgtest11`、Playwright A/B 浏览器测试，必须验证账号切换、过期请求、管理员私有文档、重复上传 409、停用账号 Token 401、RLS 无上下文默认拒绝。
- 故障注入：Worker 暂停恢复、模型调用失败与 unknown usage 保留预留、数据备份恢复后的 Flow ID、聊天记忆与密钥重连。
- 分开验证 PostgreSQL 恢复、SmartBiz 上传文件备份、Langflow 独立数据卷备份、Flow JSON 导入和 Caddy HTTPS 证书/配置。
- 绝不在客户生产租户执行破坏性 Playwright 或恢复测试。

## 7. 交付清单

- 软件版本、Git SHA、Docker image digest、Alembic current/head；
- Caddy HTTPS 域名、证书续期、网络暴露清单；
- 测试企业隔离报告、管理员操作手册、账号交接与 MFA/SSO 后续计划；
- Flow ID 库存、无凭据 JSON 导出、工具访问控制清单；
- 数据保留策略、备份周期与恢复验证、灾难预案；
- 供应商账单凭据来源、已核实/差异/缺失的调用数量与定价版本；
- 模型调用成本上限、事故响应、报警收件人实测结果；
- 第三方软件许可与客户服务条款、隐私与数据处理协议（须由专业人员审核）。

**交付结论：** 本课能生成只读交付前检查与安全设计文档，不等于符合所有法律/财税要求，也不自动签发合同或收款。
