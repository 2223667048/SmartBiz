"""Lesson 8: tenant-bounded rate limits, PG RLS.

Revision ID: 0008_pg_ops
Revises: 0007_pg_tenant
"""
from alembic import op
revision='0008_pg_ops'
down_revision='0007_pg_tenant'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE tenant_rate_limits (
      organization_id text NOT NULL REFERENCES organizations(id),
      scope text NOT NULL CHECK(scope IN ('ask','search','upload')),
      window_start bigint NOT NULL,
      hits int NOT NULL DEFAULT 0,
      PRIMARY KEY(organization_id,scope,window_start))""")
    op.execute('CREATE INDEX idx_pg_rate_cleanup ON tenant_rate_limits(window_start)')
    op.execute('ALTER TABLE tenant_rate_limits ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE tenant_rate_limits FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY tenant_rate_policy ON tenant_rate_limits FOR ALL TO smartbiz_app
      USING(organization_id=nullif(current_setting('app.tenant_id',true),''))
      WITH CHECK(organization_id=nullif(current_setting('app.tenant_id',true),''))""")
    op.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON tenant_rate_limits TO smartbiz_app')
    # Lesson 7 had table privileges for smartbiz_worker but no RLS policies.
    # The worker is a trusted backend process that must pick jobs from all tenants.
    # Keep its credential OUT of the web container; it has cross-tenant data rights.
    for tab in ('organization_documents','organization_chunks','indexing_jobs'):
        op.execute(f"CREATE POLICY worker_read ON {tab} FOR SELECT TO smartbiz_worker USING (true)")
    for tab in ('organization_documents','indexing_jobs'):
        op.execute(f"CREATE POLICY worker_update ON {tab} FOR UPDATE TO smartbiz_worker USING (true) WITH CHECK (true)")
    op.execute('CREATE POLICY worker_chunk_delete ON organization_chunks FOR DELETE TO smartbiz_worker USING (true)')
    op.execute('CREATE POLICY worker_chunk_insert ON organization_chunks FOR INSERT TO smartbiz_worker WITH CHECK (true)')
    op.execute('CREATE POLICY worker_audit_insert ON audit_events FOR INSERT TO smartbiz_worker WITH CHECK (organization_id IS NOT NULL)')


def downgrade():
    for tab in ('organization_documents','organization_chunks','indexing_jobs'):
        op.execute(f'DROP POLICY IF EXISTS worker_read ON {tab}')
    for tab in ('organization_documents','indexing_jobs'):
        op.execute(f'DROP POLICY IF EXISTS worker_update ON {tab}')
    op.execute('DROP POLICY IF EXISTS worker_chunk_delete ON organization_chunks')
    op.execute('DROP POLICY IF EXISTS worker_chunk_insert ON organization_chunks')
    op.execute('DROP POLICY IF EXISTS worker_audit_insert ON audit_events')
    op.execute('DROP TABLE IF EXISTS tenant_rate_limits')
