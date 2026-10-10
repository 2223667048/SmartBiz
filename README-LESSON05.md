# 第五课：SmartBiz RAG 管理后台与云端 Embeddings 索引重建

本包是**第四课已通过 30 项测试**的 SmartBiz 工程之增量补丁。含文档上传/列表/删除、网络上传进度、问答与引用、管理员会话、索引版本管理、云端模型连通探测、事务性重建、测试和中文教程。

- 逐步学习：`docs/08-第五课-知识库后台与云端向量重建.md`
- Windows 安装：备份，下载增量 zip，解压到 `C:\AI\smartbiz-agent-starter`，执行 `docker compose up -d --build --no-deps smartbiz`
- 访问管理后台：`http://127.0.0.1:8000/admin`
- 管理员凭证：第四课已配置的 `ADMIN_API_KEY`。无需把密钥放入浏览器 localStorage。
- 云端检查：`scripts/lesson05-cloud-check.ps1`；测试嵌入模型 **不会**修改当前索引。
- 云端索引重建：先进行 SQLite 一致性备份、配置新云端 Embeddings、确认连接，然后在管理后台重建；**重建可能调用付费 API**。
- 测试：`python -m pytest -q`。

本课使用原生 HTML/CSS/JS，不引入 React/Vue 构建依赖。**仅用于本地教学与受控演示**，仍不具备多租户/文档 ACL/正式管理员 SSO 等商用安全前提。
