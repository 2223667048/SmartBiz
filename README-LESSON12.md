# 第十二课增量升级包

优先读 `docs/15-第十二课-Langflow恢复与商业交付验收.md`。

本次**无数据库结构迁移**，继续使用 `0011_commercial`。不改动任何数据库表、不自动恢复 Langflow、不执行真实支付。

## Windows 流程

1. `cd C:\Users\Administrator\Desktop\AI-Jump\smartbiz-agent-starter`
2. 停止对 Langflow 的重建/清理，`docker ps -a` 与 `docker volume ls` 保护现场。
3. `powershell -ExecutionPolicy Bypass -File .\scripts\lesson12-langflow-diagnose.ps1`。
4. Langflow 停机后，按实际卷名备份：`powershell -ExecutionPolicy Bypass -File .\scripts\lesson12-langflow-volume-backup.ps1 -VolumeName 实际卷名`。
5. 只有恢复 SQLite 数据库、密钥和上传文件并确认完整后，才考虑额外叠加 `docker-compose.langflow-safe.yml`。
6. 复制 `examples/flow-inventory.example.json` 为 `examples/flow-inventory.local.json`，填写真实 Flow ID、模型调用路由和人工验证日期。勿放入密钥。该文件不得提交仓库。
7. `python -m pytest -q tests/test_lesson12.py`；全部测试使用 `python -m pytest -q`。
8. 已有 pgtest11 可添加跑 `tests/test_lesson12_pg_live.py`。测试依赖独立的临时数据库，不得在生产 DB 运行。
9. 运营只读报告：
   `docker compose @dc11 run --rm --no-deps -v "${PWD}:/srv/app" -w /srv/app migrate python -m app.delivery_operator --org <org-uuid> --period 2026-10 --inventory examples/flow-inventory.local.json`
10. 人工检查备份恢复、跨租户安全、费用差异和流量后，才可在受控试点执行 `--backup-restored --tenant-e2e-passed --approved`。这不是商用安全或财务认证。

## 文件

- `app/delivery_control.py` — 纯逻辑：flow 清单检查、已计量调用对账比率、发布阻断、支付状态设计。
- `app/delivery_operator.py` — 只读 PostgreSQL 报告（依赖迁移运维角色，不开放公网 API）。
- `docker-compose.langflow-safe.yml` — 独立**可选**覆盖配置，继承现有持久化卷；未经恢复确认不要启用。
- `.env.langflow-safe.example` — 预先验证并固定 Langflow 镜像版本。
- `scripts/lesson12-langflow-diagnose.ps1` — read-only 诊断旧卷与旧容器。
- `scripts/lesson12-langflow-volume-backup.ps1` — 只读快照候选卷，要求停 Langflow。
- `scripts/lesson12-release-preflight.ps1` — 检查 Compose、迁移和只读统计；不自动发布。
- `docs/15-第十二课-Langflow恢复与商业交付验收.md` — 之前 Langflow 操作总结、Flow ID / 密钥配置、可恢复性和客户验收路径。
- `docs/16-客户交付验收模板.md` — 客户交付核验签署模板。
- `tests/test_lesson12.py` 和 `tests/test_lesson12_pg_live.py` — 新增测试。

## 明确限制

- 本课没有接入支付服务商、不开具正式发票。
- 运营 Flow 清单是**人工登记**，不能自动发现未经过代理的调用，覆盖率不能号称 100%。
- 独立恢复测试、实际 Chromium E2E 和 PostgreSQL 集成测试必须在用户的 Docker 环境执行。
- 恢复老 Langflow SQLite 有兼容性风险；不要用新空 DB 覆盖老卷。
