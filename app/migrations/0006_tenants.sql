-- Tenant platform. Separate namespace from legacy kb_*. No automatic cross-tenant import.
CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS organization_users (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id),
  email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','member')),
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_org_users_org ON organization_users(organization_id);
CREATE TABLE IF NOT EXISTS organization_tokens (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES organization_users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_org_tokens_expiry ON organization_tokens(expires_at);
CREATE TABLE IF NOT EXISTS auth_login_attempts (
  principal_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS organization_documents (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  uploader_id TEXT NOT NULL REFERENCES organization_users(id),
  filename TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'organization' CHECK (visibility IN ('organization','admin')),
  storage_path TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed','deleted')),
  embedding_spec TEXT, chunk_count INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(id, organization_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_doc_active_digest ON organization_documents(organization_id,sha256) WHERE status!='deleted';
CREATE INDEX IF NOT EXISTS idx_org_documents_tenant ON organization_documents(organization_id,status);
CREATE TABLE IF NOT EXISTS organization_chunks (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  document_id TEXT NOT NULL REFERENCES organization_documents(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, page INTEGER,
  content TEXT NOT NULL, vector BLOB NOT NULL, dimension INTEGER NOT NULL,
  UNIQUE(document_id,position)
);
CREATE INDEX IF NOT EXISTS idx_org_chunks_tenant ON organization_chunks(organization_id,document_id);
CREATE TABLE IF NOT EXISTS indexing_jobs (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  document_id TEXT NOT NULL REFERENCES organization_documents(id),
  state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','canceled')),
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  worker_id TEXT, lease_until INTEGER, error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_index_jobs_tenant ON indexing_jobs(organization_id,created_at);
CREATE INDEX IF NOT EXISTS idx_index_jobs_queue ON indexing_jobs(state,lease_until,created_at);
