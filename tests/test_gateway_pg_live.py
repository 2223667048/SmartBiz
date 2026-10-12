"""Real DB integration (opt in only on isolated test database).

Requires RUN_PG_LIVE_TESTS=1; does NOT create/drop schemas or users.
"""
import os
import uuid
from datetime import datetime,timezone
from decimal import Decimal
import pytest
pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_LIVE_TESTS')!='1',reason='requires isolated PostgreSQL test database')


def test_gateway_budget_atomic_and_tenant_rls():
    from app import tenant_store_pg as store, gateway_ledger_pg as ledger
    a={'id':'test-user-a','organization_id':str(uuid.uuid4()),'role':'member'}
    b={'id':'test-user-b','organization_id':str(uuid.uuid4()),'role':'member'}
    # No cleanup for production data; run on dedicated disposable testing DB only.
    with store._tx() as conn:
        conn.execute('INSERT INTO organizations(id,name) VALUES(%s,%s),(%s,%s)',
                     (a['organization_id'],'Gateway test A',b['organization_id'],'Gateway test B'))
    today=datetime.now(timezone.utc).date()
    price={'input':Decimal('0.4'),'output':Decimal('1.6'),'cached':Decimal('0.1')}
    aid=ledger.reserve(a,str(uuid.uuid4()),'test',today,100,70,price)
    with pytest.raises(ledger.BudgetExceeded):
        ledger.reserve(a,str(uuid.uuid4()),'test',today,100,70,price)
    bid=ledger.reserve(b,str(uuid.uuid4()),'test',today,100,70,price)
    ledger.succeed(a,aid,'provider-test',{'prompt_tokens':50,'completion_tokens':5,'cached_prompt_tokens':0},28)
    report_a=ledger.daily_summary({**a,'role':'admin'})
    report_b=ledger.daily_summary({**b,'role':'admin'})
    assert report_a['daily'][0]['used_micro_usd']==28
    assert report_b['daily'][0]['held_micro_usd']==70
    with store._tx(b['organization_id']) as conn:
        assert conn.execute('SELECT id FROM model_gateway_calls WHERE id=%s',(aid,)).fetchone() is None
