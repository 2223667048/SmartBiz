"""Runs ONLY against the disposable pgtest11 PostgreSQL cluster, never production."""
import os
import uuid
import pytest
pytestmark=pytest.mark.skipif(
    os.getenv('RUN_PG_LIVE_TESTS')!='1' or os.getenv('SMARTBIZ_TEST_CLUSTER')!='lesson11',
    reason='requires disposable pgtest11 test cluster')


def test_delivery_report_and_no_tenant_context():
    from app import tenant_store_pg as store
    from app.commercial_operator import owner_connect
    from app.delivery_operator import report_rows
    suffix=uuid.uuid4().hex[:9]
    a=store.create_org('delivery-a-'+suffix,'delivery-a-'+suffix+'@example.invalid','Unique-Password-123456!-For-Test')
    b=store.create_org('delivery-b-'+suffix,'delivery-b-'+suffix+'@example.invalid','Unique-Password-123456!-For-Test')
    # RLS selects must default deny. No created docs from either enterprise.
    with store._tx() as conn:
        assert conn.execute('SELECT count(*) AS c FROM organization_documents').fetchone()['c']==0
        assert conn.execute('SELECT count(*) AS c FROM model_gateway_calls').fetchone()['c']==0
    # Creating a document for A as an application-role insert and confirming B cannot read it
    # requires the separate end-to-end test suite; never fake document ownership here.
    from datetime import datetime,timezone
    with owner_connect() as conn:
        current=datetime.now(timezone.utc).strftime('%Y-%m')
        assert report_rows(conn,a['organization_id'],current)==[]
        assert report_rows(conn,b['organization_id'],current)==[]
