# SmartBiz 第二课 → 第三课增量补丁

请先阅读 `docs/06-第三课-真实模型-记忆-工具调用.md`。

- 已有 `.env` 不覆盖；本补丁不包含模型服务密钥。
- `app/main.py` 覆盖前请备份。必须先完成第二课，否则该文件可能与旧版本不匹配。
- 新增只读公开演示接口：`GET /api/catalog/search?q=知识库`。
- 将 `extensions/smartbiz_catalog_tool.py` 的**整个文件**粘贴到 Langflow `New Custom Component` 编辑器并打开 Tool Mode，连接 Agent Tools。
- 本地测试无法证明已连接真实模型；请按文档在自己的 Docker 环境进行 Playground 验收。
