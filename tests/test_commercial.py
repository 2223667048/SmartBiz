import json
import uuid
from decimal import Decimal
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_draft_costs_are_provisional_and_not_invoice():
    from app.commercial_core import draft_invoice
    d=draft_invoice(9900000,2000000,2500000)
    assert d['provisional_overage_micro_usd']==600000
    assert d['illustrative_total_micro_usd']==10500000
    assert d['payable_micro_usd'] is None and d['not_an_invoice']
    assert draft_invoice(9900000,2000000,1000000)['provisional_overage_micro_usd']==0
    with pytest.raises(ValueError):draft_invoice(-1,0,0)


def test_reconciliation_only_one_exact_provider_id():
    from app.commercial_core import classify_reconciliation
    row={'id':'call','organization_id':'org','calculated_micro_usd':100}
    assert classify_reconciliation([row],100)['status']=='matched'
    assert classify_reconciliation([row],112)['delta']==12
    assert classify_reconciliation([],100)['status']=='unmatched'
    assert classify_reconciliation([row,row],100)['status']=='ambiguous'


def test_csv_parser_strict_and_no_duplicate():
    from app.commercial_operator import parse_provider_csv
    assert parse_provider_csv(b'provider_request_id,billed_micro_usd\np-1,150\n')==[('p-1',150)]
    with pytest.raises(ValueError):parse_provider_csv(b'provider_request_id,billed_micro_usd\np-1,150\np-1,151\n')
    with pytest.raises(ValueError):parse_provider_csv(b'provider_request_id,billed_micro_usd\np-1,-1\n')
    with pytest.raises(ValueError):parse_provider_csv(b'provider_request_id,billed_usd\np-1,150\n')


def test_alias_config_disallows_duplicates_and_negative_prices(monkeypatch):
    from app.commercial_proxy import configured_routes
    from app.model_gateway import ConfigurationError
    monkeypatch.setenv('GATEWAY_PROVIDER_BASE_URL','https://api.openai.com/v1')
    monkeypatch.setenv('GATEWAY_ALLOWED_HOSTS','api.openai.com')
    monkeypatch.setenv('GATEWAY_PROVIDER_API_KEY','test')
    monkeypatch.setenv('GATEWAY_MODEL','gpt-4.1-mini')
    for k,v in {'INPUT':'0.4','CACHED':'0.1','OUTPUT':'1.6'}.items():monkeypatch.setenv('GATEWAY_PRICE_'+k+'_USD_PER_1M',v)
    monkeypatch.setenv('GATEWAY_ROUTES_JSON',json.dumps([{'alias':'standard','model':'gpt-4.1-mini','input':'0.4','cached':'0.1','output':'1.6'}]))
    assert configured_routes()['standard']['prices']['output']==Decimal('1.6')
    monkeypatch.setenv('GATEWAY_ROUTES_JSON',json.dumps([{'alias':'bad','model':'m','input':'-1','cached':'0','output':'0'}]))
    with pytest.raises(ConfigurationError):configured_routes()
    monkeypatch.setenv('GATEWAY_ROUTES_JSON','{"bad": true}')
    with pytest.raises(ConfigurationError):configured_routes()


def test_chat_proxy_buffers_stream_and_rejects_multimodal_and_unapproved_tools():
    from app.commercial_proxy import ProxyRequest,buffered_sse,make_payload
    route={'model':'gpt-test'}
    good=ProxyRequest(model='standard',messages=[{'role':'user','content':'hello'}],tools=[{'type':'function','function':{'name':'calc','parameters':{'type':'object'}}}])
    assert make_payload(good,route)['tools'][0]['type']=='function'
    assert make_payload(ProxyRequest(model='standard',messages=[{'role':'user','content':'x'}],max_tokens=12),route)['max_completion_tokens']==12
    assert make_payload(ProxyRequest(model='standard',messages=[{'role':'user','content':'x'}],stream=True),route)['stream'] is False
    events=''.join(buffered_sse({'id':'r1','model':'m','created':1,'choices':[{
        'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}]}))
    assert 'chat.completion.chunk' in events and '"content":"ok"' in events
    assert events.endswith('data: [DONE]\n\n')
    for bad in (
        {'messages':[{'role':'user','content':[{'type':'image_url'}]}]},
        {'messages':[{'role':'user','content':'hello'}],'tools':[{'type':'web_search'}]}
    ):
        with pytest.raises(HTTPException):make_payload(ProxyRequest(model='standard',**bad),route)


def test_proxy_auth_rejects_without_key():
    from app.main import app
    with TestClient(app) as c:
        assert c.get('/v1/models').status_code==401
        assert c.post('/v1/chat/completions',json={'model':'standard','messages':[{'role':'user','content':'x'}]}).status_code==401


def test_migration_and_ops_contract():
    root=Path(__file__).resolve().parents[1]
    text=(root/'alembic/versions/0011_commercial_control.py').read_text()
    assert '0010_gateway_usage' in text
    assert 'FORCE ROW LEVEL SECURITY' in text
    assert 'commercial_lookup_proxy_key' in text
    assert 'commercial_subscription_active' in text
    assert 'commercial_default_subscription' in text
    assert 'smartbiz_owner' in text
