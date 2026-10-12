"""Portable test suite: frontend no cross-user stale render; PostgreSQL migration source contracts.
A running PostgreSQL database is tested separately by test_tenant_pg_live.py.
"""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_frontend_cross_account_logout_and_late_reply():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js is unavailable; manually run tests/workspace_frontend_test.cjs')
    run=subprocess.run([node,str(ROOT/'tests/workspace_frontend_test.cjs')],cwd=ROOT,text=True,capture_output=True,timeout=15)
    assert run.returncode==0,run.stderr
    assert 'PASS' in run.stdout


def test_postgresql_schema_enforces_rls_and_distinct_users():
    script=(ROOT/'alembic/versions/0007_pg_tenant_rls_audit.py').read_text()
    for table in ('organization_documents','organization_chunks','indexing_jobs','audit_events'):
        assert 'ALTER TABLE' in script and table in script
    assert 'FORCE ROW LEVEL SECURITY' in script
    assert 'current_setting(\'app.tenant_id\',true)' in script
    assert "visibility='organization'" in script
    assert 'CREATE POLICY chunk_read' in script
    assert 'CREATE POLICY job_read' in script
    assert 'GRANT SELECT,INSERT ON audit_events' in script
    assert 'CREATE INDEX idx_pg_vector_1536' in script
    assert 'CREATE EXTENSION IF NOT EXISTS vector' in script


def test_postgresql_runtime_contract_and_sql_boundaries():
    runtime=(ROOT/'app/tenant_store_pg.py').read_text()
    assert "set_config('app.tenant_id', %s, true)" in runtime
    assert "set_config('app.actor_role', %s, true)" in runtime
    assert 'FOR UPDATE OF j SKIP LOCKED' in runtime
    assert 'PG_APP_DSN must use smartbiz_app' in runtime
    assert 'pgvector 仅支持' in runtime
    assert "ON CONFLICT" in runtime
    assert "json.dumps(details or {})" in runtime


def test_separate_pg_secrets_not_in_service_env_file():
    yaml=(ROOT/'docker-compose.pg.yml').read_text()
    assert 'PG_MIGRATION_DSN:' in yaml and 'PG_APP_DSN:' in yaml and 'PG_WORKER_DSN:' in yaml
    assert 'PG_WORKER_DSN: postgresql://smartbiz_worker' in yaml
    assert 'PG_APP_DSN: postgresql://smartbiz_app' in yaml
    assert (ROOT/'.env.pg.example').exists()
    assert '.env.pg' in (ROOT/'.gitignore').read_text()


def test_cross_tenant_ui_is_never_cached_and_has_abort_guard():
    js=(ROOT/'web/workspace.js').read_text()
    assert 'authEpoch++' in js
    assert 'controller.abort()' not in js  # bulk reset aborts ALL controllers
    assert 'ctl.abort()' in js
    assert 'clearPrivateUI()' in js
    assert "cache:'no-store'" in js
    assert "token = '" in js and "actor = null;" in js


def test_rag_uses_fresh_llm_session_for_every_request(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import tenant_store as store, tenant_worker
    monkeypatch.setenv('SMARTBIZ_DB_PATH', str(tmp_path/'test.db'))
    monkeypatch.setenv('SMARTBIZ_UPLOAD_DIR', str(tmp_path/'uploads'))
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    monkeypatch.setenv('RAG_EMBEDDING_MODE','demo')
    monkeypatch.setenv('RAG_MIN_SIMILARITY','0.0')
    monkeypatch.setenv('AGENT_MODE','langflow')
    with TestClient(app) as client:
        store.create_org('验证','test-session@example.com','AnExamplePassword!123')
        login=client.post('/api/v2/auth/login',json={'email':'test-session@example.com','password':'AnExamplePassword!123'}).json()
        headers={'Authorization':'Bearer '+login['access_token']}
        response=client.post('/api/v2/documents',headers=headers,files={'file':('support.md','每周一到周五客服时间09:00到18:00。'.encode())})
        assert response.status_code==202
        assert tenant_worker.process_one('unit-tenant_pg')
        sessions=[]
        async def agent(prompt,session_id):
            sessions.append(session_id)
            return '每周一到周五。[1]'
        monkeypatch.setattr('app.tenant_api.ask_agent',agent)
        for _ in range(2):
            answer=client.post('/api/v2/ask',headers=headers,json={'question':'客服时间'})
            assert answer.status_code==200,answer.text
        assert len(sessions)==2 and sessions[0]!=sessions[1]
        assert ':rag:' in sessions[0]


def test_private_api_and_workspace_disable_http_caching(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('SMARTBIZ_DB_PATH',str(tmp_path/'test.db'))
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    with TestClient(app) as c:
        for path in ('/workspace','/static/workspace.js','/api/v2/documents','/api/v2/auth/login'):
            r=c.get(path)
            assert 'no-store' in r.headers.get('Cache-Control',''),path
