-- Tenant security: persistent, tenant-wide request rate counters. Not a production API gateway replacement.
CREATE TABLE IF NOT EXISTS tenant_rate_limits (
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  scope TEXT NOT NULL CHECK(scope IN ('ask','search','upload')),
  window_start INTEGER NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(organization_id,scope,window_start)
);
CREATE INDEX IF NOT EXISTS idx_tenant_rates_cleanup ON tenant_rate_limits(window_start);
