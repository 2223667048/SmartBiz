"""Read-only PostgreSQL delivery report, run exclusively with migrate owner DSN.

Usage totals here cover gateway calls only. External invoices are not fetched.
"""
import argparse
import json
from pathlib import Path
from .delivery_control import check_flow_inventory, gateway_reconciliation_summary, release_gate


def report_rows(conn, org: str, period: str):
    # Only retrieve one operator-selected tenant and monthly period.
    return [dict(r) for r in conn.execute('''
        SELECT c.id,c.status,c.provider_request_id,c.calculated_micro_usd,
          (SELECT CASE WHEN count(*)=1 THEN max(rr.status) ELSE 'ambiguous' END
           FROM commercial_reconciliation_rows rr WHERE rr.gateway_call_id=c.id) reconciliation
        FROM model_gateway_calls c WHERE c.organization_id=%s
          AND to_char(c.utc_day,'YYYY-MM')=%s ORDER BY c.created_at
        LIMIT 100000
    ''',(org,period)).fetchall()]


def main():
    p=argparse.ArgumentParser(description='Read-only delivery gate (does not issue an invoice)')
    p.add_argument('--org',required=True)
    p.add_argument('--period',required=True,help='YYYY-MM')
    p.add_argument('--inventory',required=True,help='reviewed JSON inventory path INSIDE container')
    p.add_argument('--backup-restored',action='store_true')
    p.add_argument('--tenant-e2e-passed',action='store_true')
    p.add_argument('--approved',action='store_true')
    args=p.parse_args()
    import datetime
    datetime.datetime.strptime(args.period,'%Y-%m')
    records=json.loads(Path(args.inventory).read_text(encoding='utf-8'))
    inventory=check_flow_inventory(records)
    from .commercial_operator import owner_connect
    with owner_connect() as conn:
        rows=report_rows(conn,args.org,args.period)
    gateway=gateway_reconciliation_summary(rows)
    decision=release_gate(inventory=inventory,gateway=gateway,
                          backup_restored=args.backup_restored,
                          tenant_e2e_passed=args.tenant_e2e_passed,
                          approved=args.approved)
    output={'period':args.period,'organization_id':args.org,
            'flows':inventory,'gateway':gateway,'gate':decision,
            'note':'统计仅覆盖此租户当月网关记录；Langflow 非网关调用、Embedding 和其他供应商账户均不包含'}
    print(json.dumps(output,ensure_ascii=False,indent=2,default=str))
    return 0 if decision['decision']=='ready_for_controlled_canary' else 2

if __name__ == '__main__':
    raise SystemExit(main())
