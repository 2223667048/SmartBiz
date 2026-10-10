import json
import os
import types
import pytest
from fastapi.testclient import TestClient


def test_budget_validation(monkeypatch):
    from app import cost_budget
    monkeypatch.setenv('SMARTBIZ_DAILY_ESTIMATE_USD','2.00')
    monkeypatch.setenv('SMARTBIZ_RESERVE_PER_CALL_USD','0.02')
    assert cost_budget.amounts() == (2_000_000,20_000)
    monkeypatch.setenv('SMARTBIZ_RESERVE_PER_CALL_USD','3')
    with pytest.raises(RuntimeError): cost_budget.amounts()


def test_model_budget_gate_sqlite_and_postgres(monkeypatch):
    from app import cost_budget
    calls=[]
    store=types.SimpleNamespace(reserve_model_estimate=lambda *a: calls.append(a) or False)
    actor={'organization_id':'a'}
    monkeypatch.setenv('SMARTBIZ_TENANT_BACKEND','sqlite')
    assert cost_budget.reserve(actor,store) is None
    assert calls==[]
    monkeypatch.setenv('SMARTBIZ_TENANT_BACKEND','postgres')
    with pytest.raises(Exception) as e: cost_budget.reserve(actor,store)
    assert e.value.status_code==429
    assert calls


def test_postgres_duplicate_sqlstate_mapped_to_409(monkeypatch,tmp_path):
    from app.main import app
    from app import tenant_api
    class PgConflict(Exception):
        sqlstate='23505'
        diag=types.SimpleNamespace(constraint_name='idx_pg_active_digest')
    class OtherError(Exception):
        sqlstate='42501'
        diag=types.SimpleNamespace(constraint_name='something_else')
    actor={'id':'admin','organization_id':'fake-org','role':'admin'}
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    monkeypatch.setattr(tenant_api,'_throttle',lambda actor,scope:None)
    app.dependency_overrides[tenant_api.require_manager]=lambda:actor
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            monkeypatch.setattr(tenant_api.store,'enqueue_document',lambda *args: (_ for _ in ()).throw(PgConflict()))
            r=client.post('/api/v2/documents',files={'file':('duplicate.md',b'testing','text/markdown')})
            assert r.status_code==409, r.text
            monkeypatch.setattr(tenant_api.store,'enqueue_document',lambda *args: (_ for _ in ()).throw(OtherError()))
            r=client.post('/api/v2/documents',files={'file':('other.md',b'testing','text/markdown')})
            assert r.status_code==500, r.text
    finally:
        app.dependency_overrides.clear()


def test_metrics_authentication(monkeypatch):
    from app.main import app
    monkeypatch.setenv('SMARTBIZ_METRICS_TOKEN','d'*32)
    with TestClient(app) as client:
        assert client.get('/internal/metrics').status_code==404
        response=client.get('/internal/metrics',headers={'Authorization':'Bearer '+'d'*32})
        assert response.status_code==200
        assert 'smartbiz_http_requests_total' in response.text


def test_documentation_no_exposed_ops_ports():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'docker-compose.ops.yml').read_text()
    assert 'smartbiz-prometheus-data' in text
    # Prometheus & Alertmanager are Windows loopback only; exporter has no host port.
    assert '9105:9105' not in text
    assert '127.0.0.1:9090:9090' in text
    assert '127.0.0.1:9093:9093' in text


def test_pg_migration_gives_worker_heartbeat_without_bypass_role():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'alembic/versions/0009_pg_ops_budget.py').read_text()
    assert 'tenant_model_estimates' in text
    assert 'worker_heartbeats' in text
    assert 'ALTER ROLE smartbiz_worker NOBYPASSRLS' in text
