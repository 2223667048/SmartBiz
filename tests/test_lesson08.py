"""Lesson 8: tenant rate budgets, account revocation and scoped job metrics."""
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import tenant_store as store, tenant_worker, db

PASS='AnExamplePassword-2026!!'

@pytest.fixture
def actors(tmp_path,monkeypatch):
    monkeypatch.setenv('SMARTBIZ_DB_PATH',str(tmp_path/'sandbox.sqlite3'))
    monkeypatch.setenv('SMARTBIZ_UPLOAD_DIR',str(tmp_path/'files'))
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    monkeypatch.setenv('AGENT_MODE','mock')
    monkeypatch.setenv('RAG_EMBEDDING_MODE','demo')
    monkeypatch.setenv('SMARTBIZ_SEARCH_PER_MINUTE','2')
    monkeypatch.setenv('SMARTBIZ_ASK_PER_MINUTE','2')
    monkeypatch.setenv('SMARTBIZ_UPLOAD_PER_MINUTE','2')
    with TestClient(app) as c:
        a=store.create_org('A','ops-a@example.net',PASS)
        b=store.create_org('B','ops-b@example.net',PASS)
        def head(email,password=PASS):
            r=c.post('/api/v2/auth/login',json={'email':email,'password':password})
            assert r.status_code==200,r.text
            return {'Authorization':'Bearer '+r.json()['access_token']}
        yield c,a,b,head('ops-a@example.net'),head('ops-b@example.net'),head


def test_tenant_shared_budget_and_retry_after(actors):
    c,a,b,A,B,login=actors
    for _ in range(2):
        assert c.get('/api/v2/search?q=help',headers=A).status_code==200
    r=c.get('/api/v2/search?q=help',headers=A)
    assert r.status_code==429
    assert 1<=int(r.headers['Retry-After'])<=60
    # A's other user must not bypass the tenant-wide budget.
    user=c.post('/api/v2/users',headers=A,json={'email':'other@example.net','password':PASS,'role':'member'}).json()
    other=login('other@example.net')
    assert c.get('/api/v2/search?q=help',headers=other).status_code==429
    assert c.get('/api/v2/search?q=help',headers=B).status_code==200
    assert c.post('/api/v2/ask',headers=A,json={'question':'what is the policy?'}).status_code==200
    assert c.post('/api/v2/ask',headers=A,json={'question':'what is the policy?'}).status_code==200
    assert c.post('/api/v2/ask',headers=A,json={'question':'what is the policy?'}).status_code==429
    assert c.post('/api/v2/ask',headers=B,json={'question':'what is the policy?'}).status_code==200


def test_budget_is_durable_and_window_rolls(actors):
    c,a,b,A,B,_=actors
    org=a['organization_id']
    assert store.consume_rate_limit(org,'upload',2,now=1700000000)==0
    assert store.consume_rate_limit(org,'upload',2,now=1700000001)==0
    assert store.consume_rate_limit(org,'upload',2,now=1700000002)>0
    # New window starts at the next multiple of 60.
    assert store.consume_rate_limit(org,'upload',2,now=1700000040)==0
    with db.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM tenant_rate_limits WHERE organization_id=?',(org,)).fetchone()[0]==2
    assert store.consume_rate_limit(b['organization_id'],'upload',2,now=1700000002)==0


def test_revoke_member_invalidates_token_and_cross_tenant_is_404(actors):
    c,a,b,A,B,login=actors
    created=c.post('/api/v2/users',headers=A,json={'email':'staff-a@example.net','password':PASS,'role':'member'})
    assert created.status_code==201
    uid=created.json()['id']
    staff=login('staff-a@example.net')
    assert c.get('/api/v2/auth/me',headers=staff).status_code==200
    assert c.post('/api/v2/users/'+uid+'/disable',headers=B).status_code==404
    assert c.post('/api/v2/users/'+uid+'/disable',headers=staff).status_code==403
    assert c.post('/api/v2/users/'+a['user_id']+'/disable',headers=A).status_code==409
    assert c.post('/api/v2/users/'+uid+'/disable',headers=A).json()=={'disabled':True}
    assert c.get('/api/v2/auth/me',headers=staff).status_code==401
    assert c.get('/api/v2/documents',headers=staff).status_code==401
    assert c.post('/api/v2/auth/login',json={'email':'staff-a@example.net','password':PASS}).status_code==401
    assert c.get('/api/v2/auth/me',headers=A).status_code==200


def test_job_metrics_scoped_and_manager_only(actors):
    c,a,b,A,B,login=actors
    created=c.post('/api/v2/users',headers=A,json={'email':'staff-m@example.net','password':PASS})
    assert created.status_code==201
    member=login('staff-m@example.net')
    assert c.get('/api/v2/jobs/metrics',headers=member).status_code==403
    assert c.get('/api/v2/jobs/metrics').status_code==401
    assert c.get('/api/v2/jobs/metrics',headers=B).json()['counts']=={}
    up=c.post('/api/v2/documents',headers=A,files={'file':('a.md',b'A confidential document')})
    assert up.status_code==202,up.text
    a_metrics=c.get('/api/v2/jobs/metrics',headers=A).json()
    assert a_metrics['counts']['queued']==1
    assert a_metrics['oldest_queued_seconds']>=0
    assert a_metrics['stale_leases']==0
    assert c.get('/api/v2/jobs/metrics',headers=B).json()['counts']=={}
    assert tenant_worker.process_one('unit08')
    assert c.get('/api/v2/jobs/metrics',headers=A).json()['counts']['succeeded']==1


def test_bad_limit_is_configuration_error(actors,monkeypatch):
    c,a,b,A,B,_=actors
    monkeypatch.setenv('SMARTBIZ_ASK_PER_MINUTE','invalid')
    assert c.post('/api/v2/ask',headers=A,json={'question':'hello'}).status_code==503


def test_new_pg_migration_has_rls_and_limit_grants():
    body=(Path(__file__).resolve().parents[1]/'alembic/versions/0008_pg_saas_operations.py').read_text()
    for fragment in ('ENABLE ROW LEVEL SECURITY','FORCE ROW LEVEL SECURITY',
                     "current_setting('app.tenant_id',true)",
                     'GRANT SELECT,INSERT,UPDATE,DELETE ON tenant_rate_limits TO smartbiz_app'):
        assert fragment in body


def test_audit_summary_is_admin_only_and_explicit_pg_dependency(actors):
    c,a,b,A,B,login=actors
    staff=c.post('/api/v2/users',headers=A,json={'email':'audit-member@example.net','password':PASS}).json()
    member=login('audit-member@example.net')
    assert c.get('/api/v2/audit/summary',headers=member).status_code==403
    assert c.get('/api/v2/audit/summary',headers=A).status_code==501


def test_worker_pg_rls_policies_defined_as_trusted_role():
    root=Path(__file__).resolve().parents[1]
    mig=(root/'alembic/versions/0008_pg_saas_operations.py').read_text()
    assert "CREATE POLICY worker_read" in mig
    assert "FOR SELECT TO smartbiz_worker" in mig
    assert "worker_chunk_insert" in mig
    assert "worker_chunk_delete" in mig
    assert "worker_audit_insert" in mig
    assert "FORCE ROW LEVEL SECURITY" in mig
    assert "PG_WORKER_DSN" in (root/'docker-compose.pg.yml').read_text()
