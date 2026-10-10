"""PostgreSQL multitenant schema, pgvector, RLS and audit. Fresh PG DB only.

Revision ID: 0007_pg_tenant
Revises:
"""
from alembic import op
revision = '0007_pg_tenant'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute('''CREATE TABLE organizations (
      id text PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE organization_users (
      id text PRIMARY KEY, organization_id text NOT NULL REFERENCES organizations(id),
      email text NOT NULL UNIQUE, password_hash text NOT NULL,
      role text NOT NULL CHECK (role IN ('admin','member')),
      active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE organization_tokens (
      token_hash text PRIMARY KEY, user_id text NOT NULL REFERENCES organization_users(id) ON DELETE CASCADE,
      expires_at bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE auth_login_attempts (
      principal_hash text PRIMARY KEY, failures int NOT NULL, window_started bigint NOT NULL)''')
    op.execute('''CREATE TABLE organization_documents (
      id text PRIMARY KEY, organization_id text NOT NULL REFERENCES organizations(id),
      uploader_id text NOT NULL REFERENCES organization_users(id),
      filename text NOT NULL, sha256 text NOT NULL, size_bytes bigint NOT NULL,
      visibility text NOT NULL DEFAULT 'organization' CHECK (visibility IN ('organization','admin')),
      storage_path text NOT NULL,
      status text NOT NULL CHECK (status IN ('queued','processing','ready','failed','deleted')),
      embedding_spec text, chunk_count int NOT NULL DEFAULT 0,
      error_message text, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(id,organization_id))''')
    op.execute("CREATE UNIQUE INDEX idx_pg_active_digest ON organization_documents(organization_id,sha256) WHERE status!='deleted'")
    op.execute('CREATE INDEX idx_pg_docs_org ON organization_documents(organization_id,status)')
    op.execute('''CREATE TABLE organization_chunks (
      id text PRIMARY KEY, organization_id text NOT NULL REFERENCES organizations(id),
      document_id text NOT NULL REFERENCES organization_documents(id) ON DELETE CASCADE,
      position int NOT NULL, page int, content text NOT NULL,
      embedding vector NOT NULL, dimension int NOT NULL CHECK(dimension=vector_dims(embedding)),
      UNIQUE(document_id,position))''')
    op.execute('CREATE INDEX idx_pg_chunks_doc ON organization_chunks(organization_id,document_id)')
    # Specialized ANN index only for 1536-dimensional vectors. Others use exact search.
    op.execute('''CREATE INDEX idx_pg_vector_1536 ON organization_chunks
       USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
       WHERE dimension=1536''')
    op.execute('''CREATE TABLE indexing_jobs (
      id text PRIMARY KEY, organization_id text NOT NULL REFERENCES organizations(id),
      document_id text NOT NULL REFERENCES organization_documents(id),
      state text NOT NULL CHECK(state IN ('queued','running','succeeded','failed','canceled')),
      attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL DEFAULT 3,
      worker_id text, lease_until bigint, error_message text,
      created_at timestamptz NOT NULL DEFAULT now(),
      started_at timestamptz, finished_at timestamptz)''')
    op.execute('CREATE INDEX idx_pg_jobs_queue ON indexing_jobs(state,lease_until,created_at)')
    op.execute('CREATE INDEX idx_pg_jobs_org ON indexing_jobs(organization_id,created_at)')
    op.execute('''CREATE TABLE audit_events (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      occurred_at timestamptz NOT NULL DEFAULT now(),
      organization_id text, actor_user_id text,
      action text NOT NULL, target_type text NOT NULL, target_id text,
      outcome text NOT NULL DEFAULT 'success', details jsonb NOT NULL DEFAULT '{}'::jsonb)''')
    op.execute('CREATE INDEX idx_pg_audit_org_time ON audit_events(organization_id,occurred_at DESC)')
    # Defense in depth: no tenant GUC -> no access to tenant records.
    # FORCE applies even if table owner accesses through a non-superuser role.
    for table in ('organization_documents','organization_chunks','indexing_jobs'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
    op.execute('''CREATE POLICY doc_read ON organization_documents FOR SELECT TO smartbiz_app
       USING (organization_id = nullif(current_setting('app.tenant_id',true),'')
         AND (visibility='organization' OR current_setting('app.actor_role',true)='admin'))''')
    op.execute('''CREATE POLICY doc_insert ON organization_documents FOR INSERT TO smartbiz_app
       WITH CHECK (organization_id = nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY doc_update ON organization_documents FOR UPDATE TO smartbiz_app
       USING (organization_id = nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')
       WITH CHECK (organization_id = nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY doc_delete ON organization_documents FOR DELETE TO smartbiz_app
       USING (organization_id = nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY chunk_read ON organization_chunks FOR SELECT TO smartbiz_app
       USING (organization_id = nullif(current_setting('app.tenant_id',true),'')
         AND EXISTS (SELECT 1 FROM organization_documents d WHERE d.id=document_id
           AND d.organization_id=organization_chunks.organization_id))''')
    op.execute('''CREATE POLICY chunk_insert ON organization_chunks FOR INSERT TO smartbiz_app
       WITH CHECK (organization_id=nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY chunk_delete ON organization_chunks FOR DELETE TO smartbiz_app
       USING (organization_id=nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY job_read ON indexing_jobs FOR SELECT TO smartbiz_app
       USING (organization_id=nullif(current_setting('app.tenant_id',true),'')
         AND EXISTS (SELECT 1 FROM organization_documents d WHERE d.id=document_id
           AND d.organization_id=indexing_jobs.organization_id))''')
    op.execute('''CREATE POLICY job_insert ON indexing_jobs FOR INSERT TO smartbiz_app
       WITH CHECK (organization_id=nullif(current_setting('app.tenant_id',true),'')
           AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY job_update ON indexing_jobs FOR UPDATE TO smartbiz_app
       USING (organization_id=nullif(current_setting('app.tenant_id',true),'')
           AND current_setting('app.actor_role',true)='admin')
       WITH CHECK (organization_id=nullif(current_setting('app.tenant_id',true),'')
           AND current_setting('app.actor_role',true)='admin')''')
    # App role does NOT own data, and cannot bypass RLS.
    op.execute('GRANT USAGE ON SCHEMA public TO smartbiz_app, smartbiz_worker')
    op.execute('''GRANT SELECT,INSERT,UPDATE,DELETE ON organizations,organization_users,
        organization_tokens,auth_login_attempts TO smartbiz_app''')
    op.execute('''GRANT SELECT,INSERT,UPDATE,DELETE ON organization_documents,
        organization_chunks,indexing_jobs TO smartbiz_app''')
    op.execute('GRANT SELECT,INSERT ON audit_events TO smartbiz_app')
    op.execute('GRANT USAGE ON SEQUENCE audit_events_id_seq TO smartbiz_app,smartbiz_worker')
    op.execute('''GRANT SELECT,INSERT,UPDATE,DELETE ON organization_documents,
        organization_chunks,indexing_jobs TO smartbiz_worker''')
    op.execute('GRANT INSERT ON audit_events TO smartbiz_worker')
    # Auditor must be tenant-bound. No update/delete permission for app/worker on audit events.
    op.execute('''ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY''')
    op.execute('''ALTER TABLE audit_events FORCE ROW LEVEL SECURITY''')
    op.execute('''CREATE POLICY audit_read ON audit_events FOR SELECT TO smartbiz_app
       USING(organization_id=nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')
    op.execute('''CREATE POLICY audit_insert ON audit_events FOR INSERT TO smartbiz_app
       WITH CHECK(organization_id=nullif(current_setting('app.tenant_id',true),'')
          AND current_setting('app.actor_role',true)='admin')''')


def downgrade():
    # Destructive: only run after PG backup and while all app/worker containers are stopped.
    for table in ('audit_events','indexing_jobs','organization_chunks','organization_documents',
                  'auth_login_attempts','organization_tokens','organization_users','organizations'):
        op.execute(f'DROP TABLE IF EXISTS {table} CASCADE')
    # Keep extension installed; other app tables might use pgvector.
