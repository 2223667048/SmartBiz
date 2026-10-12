"""Tenant platform: live API, tenant IDOR regression, durable queue, no legacy bypass."""
from pathlib import Path
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import tenant_store as store, tenant_worker, db, embeddings

PASS='A-Strong-Password-For-Testing-123'
RAW=('企业售后支持：工作日在线客服从09:00到18:00，紧急工单按合同办理。'*15).encode('utf-8')

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('SMARTBIZ_DB_PATH',str(tmp_path/'data.sqlite3'))
    monkeypatch.setenv('SMARTBIZ_UPLOAD_DIR',str(tmp_path/'blobs'))
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    monkeypatch.setenv('ADMIN_API_KEY','secret-for-legacy')
    monkeypatch.setenv('KB_SERVICE_KEY','secret-for-legacy-kb')
    monkeypatch.setenv('RAG_EMBEDDING_MODE','demo')
    monkeypatch.setenv('AGENT_MODE','mock')
    monkeypatch.setenv('RAG_MIN_SIMILARITY','0.0')
    with TestClient(app) as client:
        a=store.create_org('企业A','owner-a@example.com',PASS)
        b=store.create_org('企业B','owner-b@example.com',PASS)
        def login(email):
            r=client.post('/api/v2/auth/login',json={'email':email,'password':PASS})
            assert r.status_code==200,r.text
            return {'Authorization':'Bearer '+r.json()['access_token']}
        yield client,login('owner-a@example.com'),login('owner-b@example.com'),a,b


def test_auth_role_and_no_unscoped_legacy_api(env):
    c,A,B,a,b=env
    assert c.get('/health').json()=={'status':'ok'}
    assert c.get('/admin').status_code==410
    assert c.get('/api/kb/documents',headers={'X-Admin-Key':'secret-for-legacy'}).status_code==410
    assert c.get('/api/kb/search?q=secret',headers={'X-KB-Service-Key':'secret-for-legacy-kb'}).status_code==410
    assert c.get('/api/admin/leads',headers={'X-Admin-Key':'secret-for-legacy'}).status_code==410
    assert c.post('/api/sessions').status_code==410
    assert c.get('/').status_code==200 and 'SmartBiz 企业工作台' in c.get('/').text
    assert c.get('/api/v2/documents').status_code==401
    assert c.post('/api/v2/auth/login',json={'email':'owner-a@example.com','password':'bad'}).status_code==401
    assert c.get('/api/v2/auth/me',headers=A).json()['organization_id']==a['organization_id']
    user=c.post('/api/v2/users',headers=A,json={'email':'staff@example.com','password':PASS,'role':'member'})
    assert user.status_code==201,user.text
    m=c.post('/api/v2/auth/login',json={'email':'staff@example.com','password':PASS}).json()
    member={'Authorization':'Bearer '+m['access_token']}
    assert c.get('/api/v2/users',headers=member).status_code==403
    assert c.post('/api/v2/users',headers=member,json={'email':'invalid@example.com','password':PASS}).status_code==403
    assert c.post('/api/v2/documents',headers=member,files={'file':('x.md',RAW)}).status_code==403
    assert c.get('/api/v2/documents',headers=member).json()==[]
    assert c.post('/api/v2/auth/logout',headers=member).status_code==200
    assert c.get('/api/v2/auth/me',headers=member).status_code==401


def test_tenant_isolation_through_api_and_worker(env,monkeypatch):
    c,A,B,a,b=env
    r=c.post('/api/v2/documents',headers=A,files={'file':('policy.md',RAW,'text/plain')})
    assert r.status_code==202,r.text
    doc,job=r.json()['document_id'],r.json()['job_id']
    assert c.get('/api/v2/jobs/'+job,headers=B).status_code==404
    assert c.get('/api/v2/documents/'+doc,headers=B).status_code==404
    assert c.delete('/api/v2/documents/'+doc,headers=B).status_code==404
    assert c.post('/api/v2/documents/'+doc+'/retry',headers=B).status_code==404
    assert len(c.get('/api/v2/jobs',headers=B).json())==0
    assert len(c.get('/api/v2/documents',headers=B).json())==0
    assert tenant_worker.process_one('test-worker-A')
    assert c.get('/api/v2/jobs/'+job,headers=A).json()['state']=='succeeded'
    assert c.get('/api/v2/documents/'+doc,headers=A).json()['status']=='ready'
    v=c.get('/api/v2/search?q=客服时间&limit=3',headers=A)
    assert v.status_code==200,v.text
    assert v.json()['sources'] and v.json()['sources'][0]['document_id']==doc
    assert c.get('/api/v2/search?q=客服时间',headers=B).json()['sources']==[]
    assert c.post('/api/v2/ask',headers=B,json={'question':'客服时间?'}).json()['sources']==[]
    assert c.post('/api/v2/ask',headers=A,json={'question':'客服时间?'}).status_code==503 # real LLM required
    second=c.post('/api/v2/documents',headers=B,files={'file':('same-name.md',RAW,'text/plain')})
    assert second.status_code==202,second.text # same bytes accepted in OTHER tenant
    assert tenant_worker.process_one('test-worker-B')
    assert c.get('/api/v2/search?q=客服时间',headers=B).json()['sources'][0]['document_id']==second.json()['document_id']
    assert c.get('/api/v2/search?q=客服时间',headers=A).json()['sources'][0]['document_id']==doc
    assert c.post('/api/v2/documents',headers=A,files={'file':('duplicate.md',RAW)}).status_code==409
    assert c.delete('/api/v2/documents/'+doc,headers=A).status_code==204
    assert c.get('/api/v2/search?q=客服时间',headers=A).json()['sources']==[]
    assert c.get('/api/v2/search?q=客服时间',headers=B).json()['sources']
    # After soft-delete, same file can be uploaded to the SAME tenant again.
    assert c.post('/api/v2/documents',headers=A,files={'file':('again.md',RAW)}).status_code==202


def test_invalid_file_async_failure_and_manual_retry(env):
    c,A,B,a,b=env
    result=c.post('/api/v2/documents',headers=A,files={'file':('broken.pdf',b'garbage')})
    assert result.status_code==202
    job=result.json()['job_id'];doc=result.json()['document_id']
    assert tenant_worker.process_one('failure-worker')
    assert c.get('/api/v2/jobs/'+job,headers=A).json()['state']=='failed'
    assert c.get('/api/v2/documents/'+doc,headers=A).json()['status']=='failed'
    assert c.post('/api/v2/documents/'+doc+'/retry',headers=A).status_code==202
    assert tenant_worker.process_one('retry-worker')
    assert c.get('/api/v2/documents/'+doc,headers=A).json()['status']=='failed'
    assert c.post('/api/v2/documents/'+doc+'/reindex',headers=A).status_code==202 # failed may be reprocessed


def test_atomic_lease_prevents_old_worker_writes(env):
    c,A,B,a,b=env
    r=c.post('/api/v2/documents',headers=A,files={'file':('policy.md',RAW)})
    assert r.status_code==202
    j=store.claim_job('worker1')
    assert j and j['id']==r.json()['job_id']
    assert store.claim_job('worker2') is None
    vec=embeddings.demo_hash_embedding('customer-service')
    from app import kb
    chunks=kb.chunk_sections([(None,'customer-service details in this file')])
    assert c.delete('/api/v2/documents/'+j['document_id'],headers=A).status_code==204
    with pytest.raises(RuntimeError):
        store.complete_job(j,'worker1',embeddings.index_spec(),chunks,[vec])
    assert c.get('/api/v2/jobs/'+j['id'],headers=A).json()['state']=='canceled'
    assert store.claim_job('worker2') is None


def test_lease_restart_reclaim_and_stale_worker_rejected(env):
    c,A,B,a,b=env
    r=c.post('/api/v2/documents',headers=A,files={'file':('policy.md',RAW)})
    j=store.claim_job('dead-worker')
    with db.connection() as conn:
        conn.execute('UPDATE indexing_jobs SET lease_until=? WHERE id=?',(int(time.time())-1,j['id']))
    new=store.claim_job('recovery-worker')
    assert new and new['id']==j['id']
    assert c.get('/api/v2/jobs/'+j['id'],headers=A).json()['attempts']==2
    from app import kb
    chunks=kb.chunk_sections([(None,'first aid instructions for emergency')])
    vec=embeddings.demo_hash_embedding(chunks[0]['content'])
    with pytest.raises(RuntimeError):
        store.complete_job(j,'dead-worker',embeddings.index_spec(),chunks,[vec])
    store.complete_job(new,'recovery-worker',embeddings.index_spec(),chunks,[vec])
    assert c.get('/api/v2/jobs/'+j['id'],headers=A).json()['state']=='succeeded'


def test_tenant_limited_reindex_and_status(env):
    c,A,B,a,b=env
    r=c.post('/api/v2/documents',headers=A,files={'file':('policy.md',RAW)})
    tenant_worker.process_one('first')
    doc=r.json()['document_id']
    assert c.post('/api/v2/documents/'+doc+'/reindex',headers=B).status_code==404
    re=c.post('/api/v2/documents/'+doc+'/reindex',headers=A)
    assert re.status_code==202
    assert c.get('/api/v2/documents/'+doc,headers=A).json()['status']=='queued'
    assert tenant_worker.process_one('second')
    assert c.get('/api/v2/jobs/'+re.json()['job_id'],headers=A).json()['state']=='succeeded'
    assert c.get('/api/v2/documents/'+doc,headers=A).json()['status']=='ready'


def test_model_answer_tenant_namespace_isolation(env,monkeypatch):
    c,A,B,a,b=env
    for head in (A,B):
        c.post('/api/v2/documents',headers=head,files={'file':('policy.md',RAW)})
        tenant_worker.process_one('w')
    monkeypatch.setenv('AGENT_MODE','langflow')
    seen=[]
    async def fake_llm(prompt, sid):
        seen.append((prompt,sid))
        return '工作日客服为09:00-18:00。[1]'
    monkeypatch.setattr('app.tenant_api.ask_agent',fake_llm)
    for head in (A,B):
        r=c.post('/api/v2/ask',headers=head,json={'question':'工作日客服时间'})
        assert r.status_code==200,r.text
        assert r.json()['evidence_status']=='retrieved_not_verified'
        assert r.json()['sources']
    assert seen[0][1]!=seen[1][1]
    assert 'tenant:'+a['organization_id'] in seen[0][1]
    assert 'tenant:'+b['organization_id'] in seen[1][1]


def test_migration_idempotent_and_legacy_data_preserved(env):
    c,A,B,a,b=env
    from app import kb
    # Legacy KB rows still exist, but no legacy API can expose them after tenant mode.
    store.migrate();store.migrate()
    with db.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=6').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM organizations').fetchone()[0]==2
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='kb_documents'").fetchone()
    assert c.get('/api/kb/search?q=foo',headers={'X-KB-Service-Key':'secret-for-legacy-kb'}).status_code==410


def test_deleted_document_cannot_leak_to_other_user(env):
    c,A,B,a,b=env
    item=c.post('/api/v2/documents',headers=A,files={'file':('policy.md',RAW)}).json()
    tenant_worker.process_one('delete-me')
    assert c.delete('/api/v2/documents/'+item['document_id'],headers=A).status_code==204
    assert c.get('/api/v2/documents/'+item['document_id'],headers=A).status_code==404
    assert not c.get('/api/v2/search?q=客服',headers=A).json()['sources']


def test_document_visibility_role_acl_including_search_and_jobs(env):
    c,A,B,a,b=env
    created=c.post('/api/v2/users',headers=A,json={'email':'member-a@example.com','password':PASS,'role':'member'})
    assert created.status_code==201
    m=c.post('/api/v2/auth/login',json={'email':'member-a@example.com','password':PASS}).json()
    member={'Authorization':'Bearer '+m['access_token']}
    response=c.post('/api/v2/documents',headers=A,data={'visibility':'admin'},
                    files={'file':('private-admin.md',RAW)})
    assert response.status_code==202,response.text
    doc,job=response.json()['document_id'],response.json()['job_id']
    assert tenant_worker.process_one('admin-only-indexer')
    assert c.get('/api/v2/documents',headers=member).json()==[]
    assert c.get('/api/v2/documents/'+doc,headers=member).status_code==404
    assert c.get('/api/v2/jobs',headers=member).json()==[]
    assert c.get('/api/v2/jobs/'+job,headers=member).status_code==404
    assert c.get('/api/v2/search?q=工作日',headers=member).json()['sources']==[]
    assert c.post('/api/v2/ask',headers=member,json={'question':'工作日'}).json()['sources']==[]
    assert c.get('/api/v2/search?q=工作日',headers=A).json()['sources'][0]['document_id']==doc
    assert c.get('/api/v2/jobs/'+job,headers=A).status_code==200
    assert c.post('/api/v2/documents',headers=A,data={'visibility':'not-a-real-visibility'},
                    files={'file':('bad.md',RAW)}).status_code==422


def test_exhausted_expired_lease_becomes_failed_not_stuck(env):
    c,A,B,a,b=env
    result=c.post('/api/v2/documents',headers=A,files={'file':('policy.md',RAW)})
    job=result.json()['job_id']
    claimed=store.claim_job('gone-worker')
    assert claimed and claimed['id']==job
    with db.connection() as conn:
        conn.execute('UPDATE indexing_jobs SET lease_until=?,attempts=max_attempts WHERE id=?',
                     (int(time.time())-5,job))
    assert store.claim_job('new-worker') is None
    assert c.get('/api/v2/jobs/'+job,headers=A).json()['state']=='failed'
    assert c.get('/api/v2/documents/'+claimed['document_id'],headers=A).json()['status']=='failed'
    assert c.post('/api/v2/documents/'+claimed['document_id']+'/retry',headers=A).status_code==202


def test_persisted_login_rate_limit(env):
    c,A,B,a,b=env
    for _ in range(8):
        r=c.post('/api/v2/auth/login',json={'email':'owner-a@example.com','password':'wrong'})
        assert r.status_code==401
    assert c.post('/api/v2/auth/login',json={'email':'owner-a@example.com','password':PASS}).status_code==429
    # Another principal is unaffected. Existing sessions remain valid.
    assert c.get('/api/v2/auth/me',headers=A).status_code==200
    assert c.post('/api/v2/auth/login',json={'email':'owner-b@example.com','password':PASS}).status_code==200
