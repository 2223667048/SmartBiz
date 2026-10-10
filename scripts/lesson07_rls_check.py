"""Non-destructive PG RLS inspection; requires two existing org IDs.

RUN: python scripts/lesson07_rls_check.py ORG_A_ID ORG_B_ID
Only use smartbiz_app DSN. No secrets printed.
"""
import sys
from app import tenant_store_pg as pg


def check(a,b):
    assert a != b
    pg.migrate()
    with pg._tx() as c:
        row=c.execute("SELECT current_user AS account, current_setting('app.tenant_id',true) AS tenant").fetchone()
        assert row['account']=='smartbiz_app'
        for table in ('organization_documents','organization_chunks','indexing_jobs','audit_events'):
            assert c.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n']==0,table
    for current,other in ((a,b),(b,a)):
        with pg._tx(current,'admin') as c:
            for table in ('organization_documents','organization_chunks','indexing_jobs','audit_events'):
                actual=c.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE organization_id=%s',(other,)).fetchone()['n']
                assert actual==0, (table,current,other)
            role=c.execute('''SELECT rolbypassrls,rolsuper FROM pg_roles WHERE rolname=current_user''').fetchone()
            assert not role['rolbypassrls'] and not role['rolsuper']
    print('PASS: 无上下文默认拒绝；A/B 查询相互隔离；运行账号不绕过 RLS')

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('Usage: python scripts/lesson07_rls_check.py ORG_A_ID ORG_B_ID')
    check(sys.argv[1],sys.argv[2])
