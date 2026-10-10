"""Live PostgreSQL RLS and pgvector tests. Explicit opt-in on a disposable dev database.

RUN_PG_LIVE_TESTS=1 PG_APP_DSN=... PG_WORKER_DSN=... pytest -q tests/test_lesson07_pg_live.py
Do NOT point at production: creates orgs, documents and events.
"""
import os
import uuid
import pytest

pytestmark=pytest.mark.skipif(os.environ.get('RUN_PG_LIVE_TESTS')!='1',
                             reason='requires isolated PostgreSQL/pgvector dev database')


def test_live_rls_vector_and_audit(monkeypatch,tmp_path):
    from app import tenant_store_pg as pg, embeddings, kb
    monkeypatch.setenv('SMARTBIZ_UPLOAD_DIR',str(tmp_path/'uploads'))
    monkeypatch.setenv('RAG_EMBEDDING_MODE','demo')
    pg.migrate()
    unique=uuid.uuid4().hex[:12]
    A=pg.create_org('自动化企业 A',f'{unique}-a@example.com','AnExamplePassword!123')
    B=pg.create_org('自动化企业 B',f'{unique}-b@example.com','AnExamplePassword!123')
    orga,orgb=A['organization_id'],B['organization_id']
    userA={'organization_id':orga,'id':A['user_id'],'role':'admin'}
    docs=[]
    for org,u in ((orga,userA),(orgb,{'organization_id':orgb,'id':B['user_id'],'role':'admin'})):
        r=pg.enqueue_document(u,'policy.md',f'支持企业 {org} 的文档。'.encode('utf8'))
        docs.append(r['document_id'])
        job=pg.claim_job('test-w-'+org)
        assert job and job['document_id']==r['document_id']
        chunks=kb.chunk_sections([(None,'企业文档工作日客服是09:00到18:00')])
        vec=[embeddings.demo_hash_embedding(c['content']) for c in chunks]
        pg.complete_job(job,'test-w-'+org,embeddings.index_spec(),chunks,vec)
    with pg._tx() as c:
        assert c.execute('SELECT count(*) AS n FROM organization_documents').fetchone()['n']==0
    with pg._tx(orga,'admin') as c:
        assert c.execute('SELECT count(*) AS n FROM organization_documents WHERE id=%s',(docs[1],)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM organization_chunks WHERE organization_id=%s',(orgb,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM indexing_jobs WHERE organization_id=%s',(orgb,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM audit_events WHERE organization_id=%s',(orgb,)).fetchone()['n']==0
        # RLS write test is in a separate transaction to avoid a failed transaction leaking into assertions.
    with pytest.raises(Exception):
        with pg._tx(orga,'admin') as c:
            c.execute("INSERT INTO indexing_jobs(id,organization_id,document_id,state) VALUES(%s,%s,%s,'queued')",
                      (pg.new_id(),orgb,docs[1]))
    assert pg.get_document(orgb,docs[0],'admin') is None
    assert pg.get_document(orga,docs[0],'admin')
    member=pg.create_user(orga,f'{unique}-m@example.com','AnExamplePassword!123','member')
    with pg._tx(orga,'member') as c:
        assert c.execute('SELECT count(*) AS n FROM audit_events').fetchone()['n']==0
    assert pg.list_audit(orga)
    query=embeddings.demo_hash_embedding('企业文档工作日客服是09:00到18:00')
    got=pg.search(orga,'admin',query,embeddings.index_spec(),3,0)
    assert got and got[0]['document_id']==docs[0]
