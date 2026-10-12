import asyncio
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


def test_usage_cached_cost_exact():
    from app.model_gateway import calculate_cost_micro,parse_usage
    d={'usage': {'prompt_tokens':1000,'completion_tokens':250,
                 'prompt_tokens_details':{'cached_tokens':200}}}
    u=parse_usage(d)
    assert u=={'prompt_tokens':1000,'completion_tokens':250,'cached_prompt_tokens':200}
    # 800 * .4 + 200 * .1 + 250 * 1.6 = 740 micro USD
    assert calculate_cost_micro(u, {'input':Decimal('.4'),'cached':Decimal('.1'),'output':Decimal('1.6')})==740

@pytest.mark.parametrize('payload',[{}, {'usage':{}}, {'usage':{'prompt_tokens':True,'completion_tokens':1}},
    {'usage':{'prompt_tokens':-1,'completion_tokens':1}},
    {'usage':{'prompt_tokens':1,'completion_tokens':2,'prompt_tokens_details':{'cached_tokens':2}}}])
def test_missing_invalid_usage_fails_closed(payload):
    from app.model_gateway import parse_usage,UnknownUsage
    with pytest.raises(UnknownUsage):parse_usage(payload)


def test_provider_config_requires_price_and_fixed_allowed_host(monkeypatch):
    from app.model_gateway import config,ConfigurationError
    monkeypatch.setenv('GATEWAY_MODEL','gpt-4.1-mini')
    monkeypatch.setenv('GATEWAY_PROVIDER_API_KEY','secret')
    monkeypatch.setenv('GATEWAY_PROVIDER_BASE_URL','http://127.0.0.1:8080/v1')
    monkeypatch.setenv('GATEWAY_ALLOWED_HOSTS','api.openai.com')
    with pytest.raises(ConfigurationError):config()
    monkeypatch.setenv('GATEWAY_PROVIDER_BASE_URL','https://api.openai.com/v1')
    for k,v in [('INPUT','.4'),('OUTPUT','1.6'),('CACHED','.1')]:
        monkeypatch.setenv(f'GATEWAY_PRICE_{k}_USD_PER_1M',v)
    assert config()['model']=='gpt-4.1-mini'


def test_metered_router_auth_and_disabled(monkeypatch):
    from app.main import app
    from app import model_gateway_api
    monkeypatch.setenv('SMARTBIZ_MULTI_TENANT','true')
    with TestClient(app) as c:
        res=c.post('/api/v2/model/chat',json={'question':'test','request_id':'71add946-1737-40cc-8b43-7a08611bc83d'})
        assert res.status_code==401
    app.dependency_overrides[model_gateway_api.require_actor] = lambda:{'id':'u','organization_id':'o','role':'member'}
    try:
        with TestClient(app) as c:
            res=c.post('/api/v2/model/chat',json={'question':'test','request_id':'71add946-1737-40cc-8b43-7a08611bc83d'})
            assert res.status_code==503
    finally:app.dependency_overrides.clear()


def test_gateway_response_reports_provider_usage_no_prompt_saved(monkeypatch):
    from app import model_gateway_api as api
    from app.model_gateway import parse_usage
    monkeypatch.setattr(api,'_throttle',lambda actor,scope:None)
    stored=[]
    fake_ledger=SimpleNamespace(reserve=lambda *args:'call-001', succeed=lambda *args:stored.append(args),
                     BudgetExceeded=type('BudgetExceeded',(Exception,),{}), DuplicateRequest=type('DuplicateRequest',(Exception,),{}))
    monkeypatch.setattr(api,'ledger_backend',lambda:fake_ledger)
    async def fake(prompt,max_tokens,conf):
        return 'model answer',parse_usage({'usage':{'prompt_tokens':100,'completion_tokens':50}}),'provider-001'
    monkeypatch.setattr(api.mg,'provider_completion',fake)
    conf={'model':'test-model','daily_micro':2000000,'hold_micro':100000,
          'prices':{'input':Decimal('0.4'),'output':Decimal('1.6'),'cached':Decimal('0.1')}}
    actor={'id':'u','organization_id':'tenant1','role':'member'}
    result=asyncio.run(api._run_metered(actor,'PRIVATE SAMPLE', '6acc55fc-30e5-4ea5-9dbb-cdbe8c7e2750',200,conf))
    assert result['usage']['prompt_tokens']==100
    assert result['calculated_micro_usd']==120
    assert 'PRIVATE SAMPLE' not in str(stored)


def test_gateway_failure_keeps_hold(monkeypatch):
    from app import model_gateway_api as api
    from app.model_gateway import UpstreamError
    from fastapi import HTTPException
    calls=[]
    fake_ledger=SimpleNamespace(reserve=lambda *args:'held-001', uncertain=lambda *args:calls.append(args),
                     BudgetExceeded=type('BudgetExceeded',(Exception,),{}), DuplicateRequest=type('DuplicateRequest',(Exception,),{}))
    monkeypatch.setattr(api,'ledger_backend',lambda:fake_ledger)
    async def failing(*args):raise UpstreamError('remote failure')
    monkeypatch.setattr(api.mg,'provider_completion',failing)
    conf={'model':'m','daily_micro':2000000,'hold_micro':100000,'prices':{'input':Decimal('.4'),'output':Decimal('1.6'),'cached':Decimal('.1')}}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api._run_metered({'id':'u','organization_id':'org','role':'member'},'question','request',200,conf))
    assert exc.value.status_code==502
    assert len(calls)==1


def test_migration_and_release_guardrails():
    root=Path(__file__).parents[1]
    migration=(root/'alembic/versions/0010_model_gateway_usage.py').read_text()
    assert 'ROW LEVEL SECURITY' in migration
    assert 'FORCE ROW LEVEL SECURITY' in migration
    assert 'UNIQUE (organization_id, idempotency_key)' in migration
    assert (root/'.github/workflows/release.yml').exists()
    assert 'workflow_dispatch' in (root/'.github/workflows/release.yml').read_text()
