# SmartBiz 第十课交付

**入口：** `docs/13-第十课-计量模型网关-备份-负载-CICD.md`

新增模型计量网关 `POST /api/v2/model/chat`、`POST /api/v2/model/rag` 和管理报表 `GET /api/v2/model/usage`。使用 PostgreSQL RLS 保护企业账本，禁止前端提供租户 ID。根据**供应商报告 usage × 配置价格快照**计算美元，不宣称是完整供应商账单。

升级迁移：`0010_gateway_usage`。环境模板：`.env.gateway.example`（复制为 `.env.gateway` 后添加真实密钥，**不要提交 git**）。Docker：`docker-compose.gateway.yml`。恢复演练：`scripts/lesson10-backup-drill.ps1`；压力：`docker-compose.load.yml`；CI 发布：`.github/workflows`。

**限制：** 不计量原有 Langflow Agent 内部调用、Embedding 及其他 API；不支持流式、完整 Function Calling 或精确账单对账，默认不自动释放供应商状态不明的预留金额。上线之前需执行真实 PG 集成、云端模型与恢复演练。
