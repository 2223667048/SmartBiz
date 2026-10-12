"""Requires dedicated DISPOSABLE postgres-test11; never production.

Creates orgs and real account tokens; exercises RLS + security definer + quotas + operator CLI.
"""
import os
import uuid
from decimal import Decimal
import pytest
pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_LIVE_TESTS')!='1' or os.getenv('SMARTBIZ_TEST_CLUSTER')!='commercial',
                              reason='requires disposable Commercial PG test cluster')


def test_pg_subscription_proxy_reconcile_real_db(monkeypatch):
    from app import tenant_store_pg as store,commercial_operator as ops
    from app import commercial_pg,commercial_proxy
    from fastapi.testclient import TestClient
    from app.main import app
    from app.model_gateway import parse_usage
    suffix=uuid.uuid4().hex[:10]
    a=store.create_org('commercial-a-'+suffix,'a-'+suffix+'@example.invalid','Valid-Commercial-Test-Password-123!')
    b=store.create_org('commercial-b-'+suffix,'b-'+suffix+'@example.invalid','Valid-Commercial-Test-Password-123!')
    admin={'id':a['user_id'],'organization_id':a['organization_id'],'role':'admin'}
    other={'id':b['user_id'],'organization_id':b['organization_id'],'role':'admin'}
    assert commercial_pg.entitlement(admin)['plan_code']=='starter'
    assert commercial_pg.entitlement(other)['organization_id']==b['organization_id']
    with store._tx(a['organization_id'],'admin') as conn:
        assert conn.execute('SELECT count(*) AS n FROM organization_subscriptions').fetchone()['n']==1
    with pytest.raises(Exception):
        with store._tx(a['organization_id'],'admin') as conn:
            conn.execute("UPDATE organization_subscriptions SET status='active'")
    # In a separate transaction, switch to operator; never put owner secret in web env.
    with ops.owner_connect() as conn:
        ops.assign(conn,a['organization_id'],'team','active','test-operator')
        kid,secret=ops.create_key(conn,a['organization_id'],'tenant test key','test-operator')
    assert commercial_pg.entitlement(admin)['plan_code']=='team'
    monkeypatch.setenv('GATEWAY_ENABLED','true')
    monkeypatch.setenv('GATEWAY_MODEL','gpt-4.1-mini')
    monkeypatch.setenv('GATEWAY_PROVIDER_BASE_URL','https://api.openai.com/v1')
    monkeypatch.setenv('GATEWAY_PROVIDER_API_KEY','local-test-fake')
    monkeypatch.setenv('GATEWAY_ALLOWED_HOSTS','api.openai.com')
    monkeypatch.setenv('GATEWAY_PRICE_INPUT_USD_PER_1M','0.4')
    monkeypatch.setenv('GATEWAY_PRICE_CACHED_USD_PER_1M','0.1')
    monkeypatch.setenv('GATEWAY_PRICE_OUTPUT_USD_PER_1M','1.6')
    monkeypatch.delenv('GATEWAY_ROUTES_JSON',raising=False)
    provider_id='prov-'+uuid.uuid4().hex
    async def provider_fake(payload,conf):
        obj={'id':provider_id,'choices':[{'index':0,'message':{'role':'assistant','content':'hello'},'finish_reason':'stop'}],
             'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}}
        return obj,parse_usage(obj)
    monkeypatch.setattr(commercial_proxy.mg,'provider_raw_chat',provider_fake)
    with TestClient(app) as client:
        assert client.get('/v1/models',headers={'Authorization':'Bearer '+secret}).status_code==200
        resp=client.post('/v1/chat/completions',headers={'Authorization':'Bearer '+secret},
          json={'model':'tenant-default','messages':[{'role':'user','content':'hello'}],'stream':False})
        assert resp.status_code==200, resp.text
        assert resp.json()['usage']['total_tokens']==150
        call_id=resp.headers['x-smartbiz-call-id']
        assert client.get('/v1/models',headers={'Authorization':'Bearer '+'not-a-key'}).status_code==401
    with ops.owner_connect() as conn:
        from app.commercial_operator import import_claims
        result=import_claims(conn,f'provider_request_id,billed_micro_usd\n{provider_id},120\n'.encode(),
                             'trusted-test-file.csv','synthetic-fixture','test-operator')
        assert result['counts']['matched']==1
    assert commercial_pg.reconciliation_overview(admin)['rows'][0]['status']=='matched'
    assert commercial_pg.reconciliation_overview(other)['rows']==[]
    from datetime import datetime,timezone
    now=datetime.now(timezone.utc)
    draft=commercial_pg.monthly_draft(admin,now.year,now.month)
    assert draft['not_an_invoice'] and draft['payable_micro_usd'] is None
    with ops.owner_connect() as conn:
        ops.revoke_key(conn,a['organization_id'],kid,'test-operator')
    with TestClient(app) as client:
        assert client.get('/v1/models',headers={'Authorization':'Bearer '+secret}).status_code==401
    with ops.owner_connect() as conn:
        ops.assign(conn,a['organization_id'],'team','suspended','test-operator')
    assert store.login('a-'+suffix+'@example.invalid','Valid-Commercial-Test-Password-123!') is None
