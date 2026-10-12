"""Trusted operator CLI only. Requires migration OWNER DSN (never web or Worker)."""
import argparse
import csv
import hashlib
import io
import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from sqlalchemy.engine import make_url
from .commercial_core import classify_reconciliation


def owner_connect():
    raw=os.environ.get('PG_MIGRATION_DSN','')
    if not raw:raise RuntimeError('PG_MIGRATION_DSN missing; run through migrate service')
    url=make_url(raw)
    if url.username!='smartbiz_owner':raise RuntimeError('must use schema owner role')
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(url.set(drivername='postgresql').render_as_string(hide_password=False),
                           row_factory=dict_row, connect_timeout=10)


def operator_event(conn,operator,action,org=None,details=None):
    conn.execute('''INSERT INTO commercial_operator_events(id,actor,action,organization_id,details)
      VALUES (%s,%s,%s,%s,%s::jsonb)''',(str(uuid.uuid4()),operator,action,org,json.dumps(details or {})))


def list_orgs(conn):
    rows=conn.execute('''SELECT o.id,o.name,s.plan_code,s.status,s.valid_until
      FROM organizations o JOIN organization_subscriptions s ON o.id=s.organization_id
      ORDER BY o.name LIMIT 200''').fetchall()
    return [dict(r) for r in rows]


def assign(conn,org,plan,status,operator):
    if status not in ('trial','active','suspended'):raise ValueError('invalid status')
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (org,))
    changed=conn.execute('''UPDATE organization_subscriptions
      SET plan_code=%s,status=%s,updated_at=now()
      WHERE organization_id=%s RETURNING organization_id''',(plan,status,org)).fetchone()
    if not changed:raise ValueError('unknown organization')
    operator_event(conn,operator,'subscription.assign',org,{'plan':plan,'status':status})


def create_key(conn,org,label,operator):
    if not 1<=len(label)<=80:raise ValueError('bad label')
    token='sbm_'+secrets.token_urlsafe(44)
    key_id=str(uuid.uuid4())
    conn.execute('''INSERT INTO commercial_tenant_keys(id,organization_id,label,token_hash)
      VALUES(%s,%s,%s,%s)''',(key_id,org,label,hashlib.sha256(token.encode()).hexdigest()))
    operator_event(conn,operator,'tenant_key.create',org,{'key_id':key_id,'label':label})
    return key_id,token


def revoke_key(conn,org,key_id,operator):
    row=conn.execute('''UPDATE commercial_tenant_keys SET active=false,revoked_at=now()
      WHERE organization_id=%s AND id=%s AND active=true RETURNING id''',(org,key_id)).fetchone()
    if not row:raise ValueError('unknown or already revoked key')
    operator_event(conn,operator,'tenant_key.revoke',org,{'key_id':key_id})


def parse_provider_csv(raw:bytes):
    if len(raw)>5_000_000:raise ValueError('CSV too large')
    text=raw.decode('utf-8-sig')
    rdr=csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames or set(rdr.fieldnames)!={'provider_request_id','billed_micro_usd'}:
        raise ValueError('CSV columns must be provider_request_id,billed_micro_usd')
    items=[];seen=set()
    for row in rdr:
        ident=row['provider_request_id'].strip()
        val=row['billed_micro_usd'].strip()
        if not 1<=len(ident)<=160 or any(c in ident for c in '\r\n\x00') or not val.isascii() or not val.isdecimal():
            raise ValueError('bad provider request id or cost')
        amount=int(val)
        if amount>10**12 or ident in seen:raise ValueError('duplicate ID or excessive cost')
        seen.add(ident);items.append((ident,amount))
        if len(items)>10000:raise ValueError('too many rows')
    if not items:raise ValueError('empty statement')
    return items


def import_claims(conn, raw:bytes,filename:str,provider:str,operator:str):
    """CSV source MUST be independently retrieved and validated by the operator.

    We do not fetch or claim to verify official supplier invoices here.
    """
    if not 1<=len(provider)<=80:raise ValueError('invalid provider')
    entries=parse_provider_csv(raw)
    digest=hashlib.sha256(raw).hexdigest()
    batch=str(uuid.uuid4())
    conn.execute('''INSERT INTO commercial_reconciliation_batches
      (id,provider,source_filename,source_sha256,row_count) VALUES(%s,%s,%s,%s,%s)''',
      (batch,provider,filename[:150],digest,len(entries)))
    stats={'matched':0,'difference':0,'ambiguous':0,'unmatched':0}
    for provider_id,amount in entries:
        matches=list(conn.execute('''SELECT id,organization_id,calculated_micro_usd
          FROM model_gateway_calls WHERE provider_request_id=%s LIMIT 2''',(provider_id,)).fetchall())
        decision=classify_reconciliation(matches,amount)
        stats[decision['status']]+=1
        conn.execute('''INSERT INTO commercial_reconciliation_rows
          (id,batch_id,organization_id,gateway_call_id,provider_request_id,
           provider_billed_micro_usd,gateway_calculated_micro_usd,delta_micro_usd,status)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
          (str(uuid.uuid4()),batch,decision['org_id'],decision['call_id'],provider_id,
           amount,decision['gateway_cost'],decision['delta'],decision['status']))
    operator_event(conn,operator,'reconciliation.import',details={'batch':batch,'status_counts':stats})
    return {'batch_id':batch,'counts':stats,'not_officially_verified':True}


def main():
    p=argparse.ArgumentParser(description='SmartBiz platform operator; migration owner only')
    p.add_argument('--operator',required=True,help='Non-secret operator identifier for audit')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('list-orgs')
    a=sub.add_parser('assign-plan');a.add_argument('--org',required=True);a.add_argument('--plan',choices=['starter','team'],required=True);a.add_argument('--status',choices=['trial','active','suspended'],required=True)
    a=sub.add_parser('create-key');a.add_argument('--org',required=True);a.add_argument('--label',required=True)
    a=sub.add_parser('revoke-key');a.add_argument('--org',required=True);a.add_argument('--key-id',required=True)
    a=sub.add_parser('reconcile-csv');a.add_argument('--file',required=True);a.add_argument('--provider',required=True)
    args=p.parse_args()
    with owner_connect() as conn:
        if args.cmd=='list-orgs':print(json.dumps(list_orgs(conn),ensure_ascii=False,default=str,indent=2))
        elif args.cmd=='assign-plan':assign(conn,args.org,args.plan,args.status,args.operator);print('subscription updated')
        elif args.cmd=='create-key':
            kid,key=create_key(conn,args.org,args.label,args.operator)
            print('key_id:',kid)
            print('ONE-TIME MODEL API KEY (store securely; never paste into chat):',key)
        elif args.cmd=='revoke-key':revoke_key(conn,args.org,args.key_id,args.operator);print('key revoked')
        elif args.cmd=='reconcile-csv':
            file=Path(args.file)
            if not file.is_file():raise ValueError('CSV file missing inside container')
            result=import_claims(conn,file.read_bytes(),file.name,args.provider,args.operator)
            print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
