# 第六课：企业账号、多租户访问控制、持久化异步 RAG 索引

上一课全部通过后，请先阅读 `docs/09-第六课-账号租户隔离异步索引.md`，尤其是旧接口被禁用及数据库备份说明。

新增：
- `app/migrations/0006_tenants.sql`：新租户库表迁移，旧库表不修改。
- `app/tenant_store.py`：租户隔离 SQL、Argon2id 登录、Bearer 会话、文档 ACL、任务队列。
- `app/tenant_api.py`：`/api/v2/*` API。
- `app/tenant_worker.py`：独立 worker；租约、心跳、任务失败与人工重试。
- `app/tenant_admin.py`：CLI 安全创建组织管理员。
- `web/workspace.*`：企业工作台页面。
- `tests/test_lesson06.py`：权限及失败恢复测试。

主要配置：在 `.env` 增加 `SMARTBIZ_MULTI_TENANT=true`，然后
`docker compose up -d --build --force-recreate --no-deps smartbiz worker`。

启动页：`http://127.0.0.1:8000/workspace`。

**新旧数据隔离是有意的：旧知识库数据暂不归属任何新企业。** 请从本地重新上传原始文件，不能将旧公共文档自动赋权给任意租户。
