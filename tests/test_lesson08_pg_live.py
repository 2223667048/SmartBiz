"""Requires isolated PostgreSQL database and completed alembic upgrade head."""
import os
import uuid
import pytest
pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_LIVE_TESTS')!='1',
    reason='Enable only in disposable PostgreSQL environment')


def test_pg_rate_table_rls_and_access_revocation():
    from app import tenant_store_pg as pg
    pg.migrate()
    marker=uuid.uuid4().hex[:12]
    a=pg.create_org('test-A',f'08-a-{marker}@example.com','Dummy-Password-For-Test-123!!')
    b=pg.create_org('test-B',f'08-b-{marker}@example.com','Dummy-Password-For-Test-123!!')
    org=a['organization_id']
    assert pg.consume_rate_limit(org,'search',1,now=1800000010)==0
    assert pg.consume_rate_limit(org,'search',1,now=1800000011)>0
    assert pg.consume_rate_limit(b['organization_id'],'search',1,now=1800000011)==0
    with pg._tx() as c:
        assert c.execute('SELECT count(*) AS n FROM tenant_rate_limits').fetchone()['n']==0
    with pg._tx(org,'member') as c:
        assert c.execute('SELECT count(*) AS n FROM tenant_rate_limits').fetchone()['n']==1
    user=pg.create_user(org,f'08-member-{marker}@example.com','Dummy-Password-For-Test-123!!','member')
    login=pg.login(f'08-member-{marker}@example.com','Dummy-Password-For-Test-123!!')
    assert pg.actor_for_token(login['access_token']) is not None
    assert pg.disable_user(org,user['id'],a['user_id'])
    assert pg.actor_for_token(login['access_token']) is None
    assert any(x['action']=='user.disable' for x in pg.audit_summary(org)['events'])
    assert pg.job_metrics(b['organization_id'])['counts']=={}
