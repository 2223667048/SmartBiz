"""PostgreSQL transactional quota reservation and usage accounting.

Only call through the authenticated app role. Tenant identity is taken from the
actor, and the transaction's SET LOCAL RLS context is never client-controlled.
"""
import uuid
from psycopg import errors
from .tenant_store_pg import _tx
from .commercial_pg import SubscriptionInactive,PlanQuotaExceeded,enforce_monthly_quota


class BudgetExceeded(Exception):
    pass


class DuplicateRequest(Exception):
    pass


def reserve(actor, idempotency_key, model, utc_day, limit_micro, hold_micro, prices):
    org = actor['organization_id']
    call_id = str(uuid.uuid4())
    try:
        with _tx(org, actor['role']) as conn:
            enforce_monthly_quota(conn,org,hold_micro)
            # Atomic upsert increment: concurrent app workers share one quota.
            row = conn.execute('''INSERT INTO model_gateway_daily
              (organization_id,utc_day,held_micro_usd)
              VALUES (%s,%s,%s)
              ON CONFLICT (organization_id,utc_day) DO UPDATE SET
                held_micro_usd=model_gateway_daily.held_micro_usd + EXCLUDED.held_micro_usd
              WHERE model_gateway_daily.used_micro_usd +
                    model_gateway_daily.held_micro_usd + EXCLUDED.held_micro_usd <= %s
              RETURNING organization_id''', (org, utc_day, hold_micro, limit_micro)).fetchone() if hold_micro <= limit_micro else None
            if not row:
                raise BudgetExceeded('企业当天可用模型额度不足')
            conn.execute('''INSERT INTO model_gateway_calls
              (id,organization_id,actor_user_id,idempotency_key,utc_day,status,model,
               reserved_micro_usd,pricing_input_usd_per_million,pricing_output_usd_per_million,
               pricing_cached_usd_per_million)
              VALUES (%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)''',
              (call_id,org,actor['id'],idempotency_key,utc_day,model,hold_micro,
               str(prices['input']),str(prices['output']),str(prices['cached'])))
    except errors.UniqueViolation as exc:
        if exc.diag.constraint_name == 'model_gateway_calls_organization_id_idempotency_key_key':
            raise DuplicateRequest('重复的幂等请求 ID，未再次调用供应商') from None
        raise
    return call_id


def succeed(actor, call_id, provider_id, usage, cost_micro):
    with _tx(actor['organization_id'], actor['role']) as conn:
        row = conn.execute('''SELECT utc_day,reserved_micro_usd,status
          FROM model_gateway_calls WHERE id=%s AND organization_id=%s FOR UPDATE''',
          (call_id, actor['organization_id'])).fetchone()
        if not row or row['status'] != 'pending':
            raise RuntimeError('模型调用记账状态不正确')
        conn.execute('''UPDATE model_gateway_calls SET status='succeeded',provider_request_id=%s,
          calculated_micro_usd=%s,prompt_tokens=%s,completion_tokens=%s,cached_prompt_tokens=%s,
          finished_at=now() WHERE id=%s''',
          (provider_id,cost_micro,usage['prompt_tokens'],usage['completion_tokens'],
           usage['cached_prompt_tokens'],call_id))
        conn.execute('''UPDATE model_gateway_daily SET held_micro_usd=held_micro_usd-%s,
          used_micro_usd=used_micro_usd+%s,
          reported_prompt_tokens=reported_prompt_tokens+%s,
          reported_completion_tokens=reported_completion_tokens+%s,
          succeeded_calls=succeeded_calls+1
          WHERE organization_id=%s AND utc_day=%s''',
          (row['reserved_micro_usd'],cost_micro,usage['prompt_tokens'],
           usage['completion_tokens'],actor['organization_id'],row['utc_day']))


def uncertain(actor, call_id):
    # Provider may have billed even if network timed out. Retain hold; investigate!
    with _tx(actor['organization_id'], actor['role']) as conn:
        conn.execute('''UPDATE model_gateway_calls SET status='uncertain', finished_at=now()
          WHERE id=%s AND organization_id=%s AND status='pending' ''',
          (call_id,actor['organization_id']))


def daily_summary(actor, days=7):
    with _tx(actor['organization_id'],'admin') as conn:
        daily = list(conn.execute('''SELECT utc_day,used_micro_usd,held_micro_usd,
          reported_prompt_tokens,reported_completion_tokens,succeeded_calls
          FROM model_gateway_daily WHERE organization_id=%s
          AND utc_day >= (now() AT TIME ZONE 'UTC')::date - (%s::integer - 1)
          ORDER BY utc_day DESC''', (actor['organization_id'],days)).fetchall())
        calls = list(conn.execute('''SELECT id,created_at,model,status,provider_request_id,
          prompt_tokens,completion_tokens,cached_prompt_tokens,calculated_micro_usd
          FROM model_gateway_calls WHERE organization_id=%s
          ORDER BY created_at DESC LIMIT 50''',(actor['organization_id'],)).fetchall())
    return {'daily':daily,'recent_calls':calls,
            'accounting_scope':'gateway_only',
            'currency':'USD','amount_unit':'micro_usd',
            'pricing_note':'按运营商配置单价计算；并非供应商最终账单'}
