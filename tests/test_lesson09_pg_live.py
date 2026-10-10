"""Opt-in: real PostgreSQL, isolated E2E database only. Creates records."""
import os
import uuid
import pytest
pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_LIVE_TESTS')!='1',reason='Disposable PG required')


def test_upload_duplicate_http_409_and_model_budget(monkeypatch,tmp_path):
    from app import tenant_store_pg as pg,tenant_api
    from app.main import app
    from fastapi.testclient import TestClient
    pg.migrate()
    monkeypatch.setenv('SMARTBIZ_UPLOAD_DIR',str(tmp_path/'files'))
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    unique=uuid.uuid4().hex[:12]
    o=pg.create_org('Lesson 9 isolated test',f'09-{unique}@example.com','Long-Dummy-Password-123!!')
    actor={'id':o['user_id'],'organization_id':o['organization_id'],'role':'admin'}
    monkeypatch.setattr(tenant_api,'store',pg)
    monkeypatch.setattr(tenant_api,'_throttle',lambda actor,scope:None)
    app.dependency_overrides[tenant_api.require_manager]=lambda:actor
    try:
        with TestClient(app,raise_server_exceptions=False) as client:
            params={'files':{'file':('sample.md',b'Lesson 9 unique sample '+unique.encode(),'text/markdown')}}
            assert client.post('/api/v2/documents',**params).status_code==202
            r=client.post('/api/v2/documents',**params)
            assert r.status_code==409, r.text
        assert pg.reserve_model_estimate(o['organization_id'],30000,20000)
        assert not pg.reserve_model_estimate(o['organization_id'],30000,20000)
        with pg._tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM tenant_model_estimates').fetchone()['n']==0
        with pg._tx(o['organization_id'],'member') as conn:
            assert conn.execute('SELECT count(*) AS n FROM tenant_model_estimates').fetchone()['n']==1
        pg.worker_heartbeat('test-'+unique)
    finally:
        app.dependency_overrides.clear()
