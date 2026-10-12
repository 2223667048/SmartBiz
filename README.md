# SmartBiz 企业智能业务平台

SmartBiz 是面向企业场景的智能业务平台，整合多租户账号体系、企业知识库、Langflow 工作流、模型网关、用量计量、订阅控制和运维监控。后端采用 FastAPI，数据层支持 PostgreSQL、pgvector 与 Alembic，服务通过 Docker Compose 统一编排。

## 核心能力

- 企业账号、角色权限、令牌撤销与租户数据隔离
- 文档上传、异步索引、向量检索、引用式知识问答
- Langflow 工作流接入及企业专属模型路由
- OpenAI-compatible 模型代理、调用计量和预算控制
- 订阅套餐、企业密钥、用量账本与对账数据管理
- PostgreSQL RLS、审计日志、请求限流和任务状态监控
- Prometheus、Alertmanager、Caddy TLS 与发布前检查
- SQLite 本地运行模式和 PostgreSQL 服务化部署模式

## 系统结构

```text
web/                    工作台与知识库管理页面
app/main.py             FastAPI 应用入口
app/tenant_api.py       企业账号、文档、检索与问答接口
app/tenant_store_pg.py  PostgreSQL 租户数据访问与 RLS 上下文
app/tenant_worker.py    文档索引后台任务
app/embeddings.py       向量模型适配器
app/commercial_proxy.py 企业模型代理与计量兼容接口
app/model_gateway.py    上游模型调用与费用计算
app/delivery_control.py 发布与交付检查
alembic/                PostgreSQL 数据库迁移
extensions/             Langflow 自定义业务组件
scripts/                诊断、备份、恢复和发布检查脚本
tests/                  单元、接口和隔离环境集成测试
```

## 快速启动

环境要求：Docker Desktop、Docker Compose，以及可选的 Python 3.10+ 本地开发环境。

```powershell
Copy-Item .env.example .env
Copy-Item .env.pg.example .env.pg
Copy-Item .env.ops.example .env.ops
Copy-Item .env.gateway.example .env.gateway
Copy-Item .env.commercial.example .env.commercial
```

启动基础服务：

```powershell
docker compose up -d --build
```

完整 PostgreSQL 部署：

```powershell
$dc = @(
  '--env-file','.env','--env-file','.env.pg','--env-file','.env.ops',
  '--env-file','.env.gateway','--env-file','.env.commercial',
  '-f','docker-compose.yml','-f','docker-compose.pg.yml',
  '-f','docker-compose.ops.yml','-f','docker-compose.gateway.yml',
  '-f','docker-compose.commercial.yml'
)
docker compose @dc config --quiet
docker compose @dc up -d --build
```

服务入口：

- SmartBiz 工作台：<http://127.0.0.1:8000/workspace>
- OpenAPI：<http://127.0.0.1:8000/docs>
- Langflow：<http://127.0.0.1:7860>
- Prometheus：<http://127.0.0.1:9090>
- Alertmanager：<http://127.0.0.1:9093>

## Langflow 企业工作流

企业工作流通过 SmartBiz 的 OpenAI-compatible 代理调用授权模型：

```text
Base URL: http://smartbiz:8000/v1
Model: tenant-default
API Key: 企业独立的 sbm_... 密钥
```

代理支持普通响应以及 Langflow Agent 使用的流式兼容响应。上游响应完成并确认 `usage` 后才写入企业计量账本，避免流式中断造成用量遗漏。内部服务访问由 `LANGFLOW_SSRF_ALLOWED_HOSTS=smartbiz` 明确授权。

## 知识库配置

云端向量模式需要在 `.env` 中配置：

```dotenv
RAG_EMBEDDING_MODE=openai
RAG_EMBEDDING_BASE_URL=https://your-provider.example/v1
RAG_EMBEDDING_API_KEY=replace-with-secret
RAG_EMBEDDING_MODEL=your-embedding-model
RAG_EMBEDDING_DIMENSIONS=1536
```

切换模型或维度后必须重新索引已有文档。PostgreSQL 当前接受 1–2000 维有限数值向量，生产配置应固定维度，避免供应商默认值变化造成索引不兼容。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

独立 PostgreSQL 集成测试由 Compose 的测试 profile 运行，不应指向业务数据库。常用测试按能力命名，例如：

- `tests/test_tenant_security.py`
- `tests/test_gateway.py`
- `tests/test_commercial.py`
- `tests/test_delivery.py`

## 运维与安全

- `.env`、`.env.*`、数据库备份、运行日志和本地密钥不得提交到版本库。
- 不同企业必须使用不同的模型代理密钥，密钥只在创建时显示一次。
- 禁止使用 `docker compose down -v` 处理普通故障，以免删除业务卷。
- 数据库迁移、恢复和回滚前先创建可验证备份。
- 对公网部署前应完成域名、TLS、密钥托管、访问控制、备份恢复、容量和安全评估。
- 模型计量依赖供应商返回的 `usage`；未知用量请求会进入待核对状态，不会被静默忽略。
