# 第九课：先修复浏览器上传 500，再部署 HTTPS、监控和预算控制

详情见 `docs/12-第九课-上传409修复-TLS-监控-预算-告警.md`。

## 首要验收

- `docker compose @dc logs --tail 120 smartbiz` 核实原 500 的真正堆栈；不要仅依靠浏览器提示推断原因
- 测试文件内容每次不同，第一次上传应 202，相同内容重复上传应 409
- `pytest browser-e2e -q --browser chromium` 应完成跨企业与 Token 撤销测试
- PostgreSQL 执行 Alembic `0009_pg_budget` 后，核实 `smartbiz_worker` 的 `rolbypassrls=false`
- Caddy HTTPS 可访问；Prometheus scrape 正常；Web 内部 metrics 不可从 Caddy 访问
- 预算为**预估消耗预留额度**，不是供应商的实际账单或硬性费用封顶
