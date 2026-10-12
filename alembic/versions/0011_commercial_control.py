"""Lesson 11: tenant subscription, metered proxy keys and reconciliation evidence.

Revision ID: 0011_commercial
Revises: 0010_gateway_usage
"""
from alembic import op
revision = '0011_commercial'
down_revision = '0010_gateway_usage'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE commercial_plans (
      code text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_-]{1,31}$'),
      display_name text NOT NULL,
      monthly_fee_micro_usd bigint NOT NULL CHECK (monthly_fee_micro_usd>=0),
      monthly_model_cap_micro_usd bigint NOT NULL CHECK (monthly_model_cap_micro_usd>0),
      max_seats integer NOT NULL CHECK (max_seats>0),
      active boolean NOT NULL DEFAULT true)''')
    op.execute("""INSERT INTO commercial_plans(code,display_name,monthly_fee_micro_usd,monthly_model_cap_micro_usd,max_seats) VALUES
      ('starter','Starter (demo pricing)',9900000,2000000,5),
      ('team','Team (demo pricing)',49000000,20000000,25)""")
    op.execute('''CREATE TABLE organization_subscriptions (
      organization_id text PRIMARY KEY REFERENCES organizations(id),
      plan_code text NOT NULL REFERENCES commercial_plans(code),
      status text NOT NULL DEFAULT 'trial' CHECK (status IN ('trial','active','suspended')),
      valid_until timestamptz,
      updated_at timestamptz NOT NULL DEFAULT now())''')
    op.execute("""INSERT INTO organization_subscriptions(organization_id,plan_code,status)
      SELECT id,'starter','trial' FROM organizations""")
    # Owner-created trigger, narrow search_path, normal tenant role cannot forge subscriptions.
    op.execute('''CREATE FUNCTION commercial_default_subscription() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        INSERT INTO organization_subscriptions(organization_id,plan_code,status)
        VALUES (NEW.id,'starter','trial');
        RETURN NEW;
      END $$''')
    op.execute('CREATE TRIGGER commercial_org_created AFTER INSERT ON organizations FOR EACH ROW EXECUTE FUNCTION commercial_default_subscription()')
    op.execute('''CREATE TABLE commercial_tenant_keys (
      id text PRIMARY KEY,
      organization_id text NOT NULL REFERENCES organizations(id),
      label text NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
      token_hash text NOT NULL UNIQUE,
      active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz)''')
    op.execute('CREATE INDEX idx_tenant_keys_org ON commercial_tenant_keys(organization_id)')
    op.execute('''CREATE TABLE commercial_reconciliation_batches (
      id text PRIMARY KEY,
      provider text NOT NULL,
      source_filename text NOT NULL,
      source_sha256 text NOT NULL UNIQUE,
      imported_at timestamptz NOT NULL DEFAULT now(),
      row_count integer NOT NULL DEFAULT 0)''')
    op.execute('''CREATE TABLE commercial_reconciliation_rows (
      id text PRIMARY KEY,
      batch_id text NOT NULL REFERENCES commercial_reconciliation_batches(id),
      organization_id text REFERENCES organizations(id),
      gateway_call_id text REFERENCES model_gateway_calls(id),
      provider_request_id text NOT NULL,
      provider_billed_micro_usd bigint NOT NULL CHECK (provider_billed_micro_usd>=0),
      gateway_calculated_micro_usd bigint,
      delta_micro_usd bigint,
      status text NOT NULL CHECK (status IN ('matched','difference','unmatched','ambiguous')),
      UNIQUE(batch_id,provider_request_id))''')
    op.execute('CREATE INDEX idx_recon_tenant ON commercial_reconciliation_rows(organization_id)')
    op.execute('''CREATE TABLE commercial_operator_events (
       id text PRIMARY KEY, actor text NOT NULL, action text NOT NULL,
       organization_id text, details jsonb NOT NULL DEFAULT '{}'::jsonb,
       created_at timestamptz NOT NULL DEFAULT now())''')
    # Security-definer lookup is only for resolving a hashed key; other app
    # reads still require the transaction-local tenant context.
    for table in ('commercial_tenant_keys','organization_subscriptions','commercial_reconciliation_rows'):
        op.execute(f'CREATE POLICY {table}_owner ON {table} FOR ALL TO smartbiz_owner USING (true) WITH CHECK (true)')
    op.execute('CREATE POLICY gateway_calls_owner_recon ON model_gateway_calls FOR SELECT TO smartbiz_owner USING (true)')
    op.execute("""CREATE FUNCTION commercial_lookup_proxy_key(digest text)
      RETURNS TABLE(organization_id text,key_id text)
      LANGUAGE sql SECURITY DEFINER SET search_path = public,pg_temp AS $$
      SELECT k.organization_id,k.id FROM commercial_tenant_keys k
      JOIN organization_subscriptions s ON s.organization_id=k.organization_id
      WHERE k.token_hash=digest AND k.active=true AND s.status IN ('trial','active')
        AND (s.valid_until IS NULL OR s.valid_until>now())
      $$""")
    op.execute("""CREATE FUNCTION commercial_subscription_active(oid text)
      RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
      SELECT EXISTS(SELECT 1 FROM organization_subscriptions s
        WHERE s.organization_id=oid AND s.status IN ('trial','active')
        AND (s.valid_until IS NULL OR s.valid_until>now())) $$""")
    op.execute('REVOKE ALL ON FUNCTION commercial_subscription_active(text) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION commercial_subscription_active(text) TO smartbiz_app')
    op.execute('REVOKE ALL ON FUNCTION commercial_lookup_proxy_key(text) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION commercial_lookup_proxy_key(text) TO smartbiz_app')
    # Tenant isolation; master/operator CLI uses a separate schema owner connection.
    for tbl in ('organization_subscriptions','commercial_tenant_keys','commercial_reconciliation_rows'):
        op.execute(f'ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY {tbl}_isolate ON {tbl} FOR SELECT TO smartbiz_app
          USING (organization_id = nullif(current_setting('app.tenant_id', true), ''))''')
        op.execute(f'GRANT SELECT ON {tbl} TO smartbiz_app')
    op.execute('GRANT SELECT ON commercial_plans,alembic_version TO smartbiz_app')
    # Only browser/API proxy can select a key hash; actual key creation/revocation is OWNER CLI only.
    # Provider reconciliation import and financial adjustments cannot be written by smartbiz_app.


def downgrade():
    op.execute('DROP FUNCTION IF EXISTS commercial_subscription_active(text)')
    op.execute('DROP FUNCTION IF EXISTS commercial_lookup_proxy_key(text)')
    op.execute('DROP POLICY IF EXISTS gateway_calls_owner_recon ON model_gateway_calls')
    op.execute('DROP TRIGGER IF EXISTS commercial_org_created ON organizations')
    op.execute('DROP FUNCTION IF EXISTS commercial_default_subscription()')
    for t in ('commercial_reconciliation_rows','commercial_reconciliation_batches','commercial_tenant_keys',
              'organization_subscriptions','commercial_operator_events','commercial_plans'):
        op.execute(f'DROP TABLE IF EXISTS {t}')
