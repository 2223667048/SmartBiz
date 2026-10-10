"""Lesson 9: per-tenant conservative cloud-cost reservation & worker heartbeat.

Revision ID: 0009_pg_budget
Revises: 0008_pg_ops
"""
from alembic import op
revision='0009_pg_budget'
down_revision='0008_pg_ops'
branch_labels=None
depends_on=None


def upgrade():
    # Earlier init script incorrectly gave the worker BYPASSRLS. Existing volumes
    # do not re-run docker-entrypoint-initdb.d; revoke it as part of migration.
    # RLS worker policies were added in migration 0008.
    op.execute('ALTER ROLE smartbiz_worker NOBYPASSRLS')
    op.execute('''CREATE TABLE tenant_model_estimates (
      organization_id text NOT NULL REFERENCES organizations(id),
      utc_day date NOT NULL,
      reserved_micro_usd bigint NOT NULL CHECK (reserved_micro_usd >= 0),
      requests bigint NOT NULL DEFAULT 0,
      PRIMARY KEY(organization_id,utc_day))''')
    op.execute('ALTER TABLE tenant_model_estimates ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE tenant_model_estimates FORCE ROW LEVEL SECURITY')
    op.execute('''CREATE POLICY estimate_tenant_all ON tenant_model_estimates FOR ALL TO smartbiz_app
      USING(organization_id=nullif(current_setting('app.tenant_id',true),''))
      WITH CHECK(organization_id=nullif(current_setting('app.tenant_id',true),''))''')
    op.execute('GRANT SELECT,INSERT,UPDATE ON tenant_model_estimates TO smartbiz_app')
    op.execute('GRANT SELECT ON tenant_model_estimates TO smartbiz_worker')
    op.execute('CREATE POLICY estimate_worker_read ON tenant_model_estimates FOR SELECT TO smartbiz_worker USING (true)')
    op.execute('''CREATE TABLE worker_heartbeats (
      worker_id text PRIMARY KEY, seen_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('GRANT SELECT, INSERT, UPDATE ON worker_heartbeats TO smartbiz_worker')


def downgrade():
    op.execute('DROP TABLE IF EXISTS worker_heartbeats')
    op.execute('DROP TABLE IF EXISTS tenant_model_estimates')
