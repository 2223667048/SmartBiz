"""PostgreSQL tenant read side; platform mutations run via owner-only CLI.

No public endpoint creates a tenant, changes a plan, accepts payment, or issues invoices.
"""
from datetime import datetime, timezone
from .tenant_store_pg import _tx
from .commercial_core import draft_invoice


class SubscriptionInactive(Exception): pass
class PlanQuotaExceeded(Exception): pass


def entitlement(actor):
    with _tx(actor['organization_id'], actor['role']) as conn:
        row=conn.execute('''SELECT s.organization_id,s.plan_code,s.status,s.valid_until,
           p.display_name,p.monthly_fee_micro_usd,p.monthly_model_cap_micro_usd,p.max_seats
           FROM organization_subscriptions s JOIN commercial_plans p ON p.code=s.plan_code
           WHERE s.organization_id=%s''',(actor['organization_id'],)).fetchone()
        return dict(row) if row else None


def enforce_monthly_quota(conn, org: str, hold: int):
    # Serialize each tenant across concurrent workers without granting UPDATE on plans.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (org,))
    ent=conn.execute('''SELECT s.status,s.valid_until,p.monthly_model_cap_micro_usd AS cap
      FROM organization_subscriptions s JOIN commercial_plans p ON p.code=s.plan_code
      WHERE s.organization_id=%s''',(org,)).fetchone()
    if not ent or ent['status'] not in ('trial','active') or (ent['valid_until'] and ent['valid_until']<=datetime.now(timezone.utc)):
        raise SubscriptionInactive('企业订阅已失效')
    aggregate=conn.execute('''SELECT COALESCE(sum(used_micro_usd+held_micro_usd),0) AS allocated
       FROM model_gateway_daily WHERE organization_id=%s
       AND utc_day>=date_trunc('month',now() AT TIME ZONE 'UTC')::date''',(org,)).fetchone()
    if int(aggregate['allocated'])+hold>int(ent['cap']):
        raise PlanQuotaExceeded('本月模型网关额度不足')


def monthly_draft(actor, year: int, month: int):
    if actor['role']!='admin': raise PermissionError('requires tenant admin')
    if not (2020<=year<=2100 and 1<=month<=12): raise ValueError('bad billing period')
    ent=entitlement(actor)
    if not ent: raise ValueError('no subscription')
    with _tx(actor['organization_id'], 'admin') as conn:
        row=conn.execute('''SELECT COALESCE(sum(used_micro_usd),0) AS cost,
          COALESCE(sum(held_micro_usd),0) AS holds,
          COALESCE(sum(succeeded_calls),0) AS calls
          FROM model_gateway_daily WHERE organization_id=%s
          AND extract(year from utc_day)=%s AND extract(month from utc_day)=%s''',
          (actor['organization_id'],year,month)).fetchone()
        recon=conn.execute('''SELECT status,count(*) AS n
          FROM commercial_reconciliation_rows
          WHERE organization_id=%s AND gateway_call_id IN
             (SELECT id FROM model_gateway_calls WHERE organization_id=%s
              AND extract(year from utc_day)=%s AND extract(month from utc_day)=%s)
          GROUP BY status''',(actor['organization_id'],actor['organization_id'],year,month)).fetchall()
    draft=draft_invoice(int(ent['monthly_fee_micro_usd']),int(ent['monthly_model_cap_micro_usd']),int(row['cost']))
    return {**draft,'period':f'{year:04d}-{month:02d}', 'plan':ent['plan_code'],
            'held_micro_usd':int(row['holds']),'succeeded_calls':int(row['calls']),
            'reconciliation_status_counts':{r['status']:r['n'] for r in recon},
            'operator_price_snapshot_required':True}


def reconciliation_overview(actor):
    with _tx(actor['organization_id'],'admin') as conn:
        rows=conn.execute('''SELECT status,count(*) AS n,COALESCE(sum(delta_micro_usd),0) AS delta
          FROM commercial_reconciliation_rows WHERE organization_id=%s
          GROUP BY status''',(actor['organization_id'],)).fetchall()
    return {'scope':'matched_tenant_provider_rows_only','rows':[dict(r) for r in rows],
            'unmatched_not_attributed':True}
