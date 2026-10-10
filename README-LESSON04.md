# SmartBiz 第四课增量包｜RAG 知识库

本补丁用于在**已完成第一至第三课**的 SmartBiz 工程上增加：

- PDF/DOCX/TXT/MD 文件上传（管理员权限）；
- 文本抽取、500 字符切分与 80 字符重叠；
- SQLite `kb_documents` / `kb_chunks` 表；
- 零成本 demo 向量与真实 OpenAI 兼容 Embeddings HTTP 接口；
- 余弦相似检索、来源片段与 PDF 页码；
- `POST /api/kb/ask` Langflow 生成 + 来源 JSON；
- Langflow 只读知识库自定义工具；
- 原有 16 项测试 + 新增 14 项测试，共 30 项。

## 先备份，后覆盖

```powershell
cd C:\AI\smartbiz-agent-starter
New-Item -ItemType Directory -Force .\lesson04-backup
Copy-Item .\app\main.py, .\requirements.txt, .\docker-compose.yml, .\.env.example -Destination .\lesson04-backup\
Expand-Archive "$env:USERPROFILE\Downloads\smartbiz-lesson04-patch.zip" "C:\AI\smartbiz-agent-starter" -Force
notepad .env
```

在 `.env` 增加两串不同的强密钥 `ADMIN_API_KEY` / `KB_SERVICE_KEY`，并设置 `RAG_EMBEDDING_MODE=demo`。**不要覆盖 `.env` 文件，也不要上传客户机密文档。**

```powershell
docker compose up -d --build --no-deps smartbiz
docker compose up -d --force-recreate --no-deps langflow
```

随后打开 [详细教程](docs/07-第四课-RAG知识库实战.md)，按第 4～7 节执行上传、检索、问答和 Agent 工具连接。

**接口速览**：`POST /api/kb/documents`、`GET /api/kb/documents`、`DELETE /api/kb/documents/{id}`、`GET /api/kb/search?q=...`、`POST /api/kb/ask`。

**声明：** demo 哈希向量不是语义 Embedding，来源列表不代表每句回答已核验，当前知识库是共享教学库；正式商用需要 ACL/tenant、审核与生产级索引。
