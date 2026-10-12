"""Provider-reported token ledger & atomic daily tenant quotas.

Revision ID: 0010_gateway_usage
Revises: 0009_pg_budget
"""
from alembic import op
revision = '0010_gateway_usage'
down_revision = '0009_pg_budget'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE model_gateway_daily (
      organization_id text NOT NULL REFERENCES organizations(id),
      utc_day date NOT NULL,
      used_micro_usd bigint NOT NULL DEFAULT 0 CHECK (used_micro_usd>=0),
      held_micro_usd bigint NOT NULL DEFAULT 0 CHECK (held_micro_usd>=0),
      reported_prompt_tokens bigint NOT NULL DEFAULT 0,
      reported_completion_tokens bigint NOT NULL DEFAULT 0,
      succeeded_calls bigint NOT NULL DEFAULT 0,
      PRIMARY KEY (organization_id, utc_day))''')
    op.execute('''CREATE TABLE model_gateway_calls (
      id text PRIMARY KEY,
      organization_id text NOT NULL REFERENCES organizations(id),
      actor_user_id text NOT NULL,
      idempotency_key text NOT NULL,
      utc_day date NOT NULL,
      status text NOT NULL CHECK (status IN ('pending','succeeded','uncertain')),
      model text NOT NULL,
      provider_request_id text,
      reserved_micro_usd bigint NOT NULL,
      calculated_micro_usd bigint,
      prompt_tokens bigint,
      completion_tokens bigint,
      cached_prompt_tokens bigint,
      pricing_input_usd_per_million text NOT NULL,
      pricing_output_usd_per_million text NOT NULL,
      pricing_cached_usd_per_million text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      UNIQUE (organization_id, idempotency_key))''')
    op.execute('CREATE INDEX idx_gateway_calls_org_time ON model_gateway_calls(organization_id,created_at DESC)')
    for tbl in ('model_gateway_daily','model_gateway_calls'):
        op.execute(f'ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY {tbl}_tenant ON {tbl} FOR ALL TO smartbiz_app
          USING (organization_id = nullif(current_setting('app.tenant_id',true),''))
          WITH CHECK (organization_id = nullif(current_setting('app.tenant_id',true),''))''')
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON {tbl} TO smartbiz_app')
    # Only migration owner may update any tenant records outside tenant RLS context.


def downgrade():
    op.execute('DROP TABLE IF EXISTS model_gateway_calls')
    op.execute('DROP TABLE IF EXISTS model_gateway_daily')
