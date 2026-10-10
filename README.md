# 智企 AI · SmartBiz Agent MVP

基于 [Langflow](https://github.com/langflow-ai/langflow) 的**独立业务项目**。Langflow 是 MIT 开源软件；本仓库里的业务层示例可自主修改。使用原项目时仍需保留适用的 MIT 许可和版权声明。

> 这是从 0 到 1 的**可本地运行 MVP**，不是具备完整 SaaS 租户隔离的生产系统，也不是对原 Langflow 仓库的完整复制。需要互联网/镜像源以及模型服务凭证（mock 不需要）。

## 你拿到的功能
- 浏览器 AI 对话页面（默认 mock，不花费模型调用费）
- 会话 token 隔离、持久化聊天记录到 SQLite
- 客户留资（明确勾选同意）、评价、人工处理工单、简易管理员统计/查看接口
- Langflow `/api/v1/run/{flow_id}` 集成与错误保护
- pytest 自动化测试、Docker Compose、本地开发说明
- `extensions/lead_intent.py` 可复制到 Langflow Custom Component 作为学习扩展

## 推荐：零依赖 AI 服务先运行业务层（Python 3.10+）

```bash
# 进入解压后的 smartbiz-agent-starter 目录
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# macOS/Linux:
cp .env.example .env
# Windows PowerShell:
# Copy-Item .env.example .env
# .env 默认 AGENT_MODE=mock，无需模型 Key
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000**。测试用例：输入「怎么收费？」；点击回答“有帮助/没帮助”；提交客户需求（先同意），再创建一张工单。看到「演示模式」是**预期结果**，表示还没有真正连 AI。按 `Ctrl+C` 退出。

## 第二步：Langflow（Docker）

```bash
# 先安装好 Docker Desktop；修改 .env 中的 LANGFLOW_SUPERUSER_PASSWORD
# 一键启动两个容器（前提：.env 已创建）：
docker compose up --build -d
# 打开 http://127.0.0.1:7860 进入 Langflow
# 打开 http://127.0.0.1:8000 进入 SmartBiz
```

Docker Compose 模式会使用两个数据卷持久化 SQLite 和 Langflow 数据。不要同时保留前面的本地 uvicorn 服务占用 8000 端口。第一次下载镜像时需要网络；无法下载时先检查 Docker/代理，不要删除数据卷。

## 第三步：从 mock 切换真正 Agent
按 `docs/02-Langflow配置.md` 在 Langflow 建立并验证 Chat Input → Model → Chat Output 工作流，创建 Langflow API Key 和复制 Flow ID。修改根目录 `.env`：

```dotenv
AGENT_MODE=langflow
LANGFLOW_FLOW_ID=从画布获得的真实 Flow ID
LANGFLOW_API_KEY=从 Langflow 设置页生成的真实 API Key
```

如果使用 Python 启动的 SmartBiz，`LANGFLOW_URL=http://127.0.0.1:7860`；如果使用 Docker Compose，会自动覆盖为容器内的 `http://langflow:7860`。配置修改后重启（Python 重新运行；Docker `docker compose up -d --force-recreate smartbiz`）。

## 快速查看数据（管理接口仅供本地演示）

配置 `.env` 里的 `ADMIN_API_KEY` 为足够长的随机秘密并重启，再运行（替换下面示例值）：

```bash
curl -H "X-Admin-Key: REPLACE_WITH_YOUR_ADMIN_API_KEY" http://127.0.0.1:8000/api/admin/stats
curl -H "X-Admin-Key: REPLACE_WITH_YOUR_ADMIN_API_KEY" http://127.0.0.1:8000/api/admin/leads
curl -H "X-Admin-Key: REPLACE_WITH_YOUR_ADMIN_API_KEY" http://127.0.0.1:8000/api/admin/messages
curl -H "X-Admin-Key: REPLACE_WITH_YOUR_ADMIN_API_KEY" http://127.0.0.1:8000/api/admin/tickets
```

也可在 http://127.0.0.1:8000/docs 查看 OpenAPI 接口列表。严禁把后端管理员 Key 或 Langflow Key 写到浏览器代码中。

## 测试

```bash
python -m pytest -q
```

## 源码结构

```text
app/main.py        FastAPI HTTP 路由；输入验证与权限边界
app/langflow.py    Langflow 适配器（未来切换其他 Agent 框架只动这里）
app/db.py          SQLite 表结构与持久化
app/tickets.py     工单新增、查询、状态流转
app/settings.py    配置
web/               HTML/CSS/JS 聊天与留资页面
extensions/        Langflow 自定义组件示例
tests/             自动化测试
docs/              学习路线、源码地图、RAG、商业上线清单
```

## 错误处理
- `AGENT_MODE=mock` 返回“演示模式”：是正确行为，**没有真实模型推理**。
- 真正调用时返回 502：检查 Langflow 是否启动、FLOW_ID 是否正确、API Key 是否有效、Flow 是否有 Chat Output。
- 登录失败：检查 `LANGFLOW_SUPERUSER_PASSWORD` 是否强密码，且根据所用镜像配置对应用户名。
- 浏览器聊天 401：当前会话失效，新建会话。
- Docker 镜像下载失败：网络/镜像仓库问题，先单独运行 `docker pull langflowai/langflow:latest` 验证。

官方来源：
- Langflow GitHub https://github.com/langflow-ai/langflow
- API https://docs.langflow.org/api-flows-run
- Docker https://docs.langflow.org/deployment-docker
- Knowledge Base https://docs.langflow.org/knowledge
- Custom components https://docs.langflow.org/extensions-quickstart
