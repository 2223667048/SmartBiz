"""One-way, operator-controlled SQLite lesson-6 -> PostgreSQL v7 migration.

Required: stop smartbiz/worker while importing, keep the original SQLite DB unchanged,
use same /data upload volume; auth sessions intentionally NOT copied. Run --dry-run first.
"""
import argparse
from array import array
import os
import sqlite3
from pathlib import Path
from . import tenant_store_pg as pg


def source():
    path = Path(os.environ.get('SMARTBIZ_DB_PATH', '/data/smartbiz.sqlite3'))
    if not path.exists():raise SystemExit(f'SQLite 数据库不存在：{path}')
    conn=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    return conn


def rows(conn,table):
    return [dict(r) for r in conn.execute(f'SELECT * FROM {table}').fetchall()]


def run(dry_run):
    pg.migrate()
    with source() as old:
        names=('organizations','organization_users','organization_documents','organization_chunks','indexing_jobs')
        records={table:rows(old,table) for table in names}
    counts={table:len(data) for table,data in records.items()}
    print('待迁移记录数：',counts)
    missing=[d['storage_path'] for d in records['organization_documents']
             if d['status']!='deleted' and not Path(d['storage_path']).is_file()]
    if missing:
        raise RuntimeError(f'存在 {len(missing)} 个原始文件路径无效，停止迁移（例：{missing[0]}）')
    if dry_run:
        print('只读预检成功；未修改 PostgreSQL。')
        return counts
    # One PostgreSQL transaction. RLS context is set per organization when inserting documents/chunks/jobs.
    # GRANTs permit account bootstrap; document rows still need the correct tenant GUC.
    with pg._tx() as conn:
        if conn.execute('SELECT 1 FROM organizations LIMIT 1').fetchone():
            raise RuntimeError('PG 不是空库：为避免混写或冲突，不允许重复导入；请使用专门的空 PG 数据库')
        for org in records['organizations']:
            conn.execute('INSERT INTO organizations(id,name) VALUES(%s,%s)',(org['id'],org['name']))
        for user in records['organization_users']:
            conn.execute('''INSERT INTO organization_users(id,organization_id,email,password_hash,role,active)
              VALUES(%s,%s,%s,%s,%s,%s)''',
              (user['id'],user['organization_id'],user['email'],user['password_hash'],user['role'],bool(user['active'])))
        docs={d['id']:d for d in records['organization_documents']}
        for org in records['organizations']:
            org_id=org['id']
            conn.execute("SELECT set_config('app.tenant_id', %s, true)",(org_id,))
            conn.execute("SELECT set_config('app.actor_role', 'admin', true)")
            for d in records['organization_documents']:
                if d['organization_id']!=org_id:continue
                conn.execute('''INSERT INTO organization_documents
                   (id,organization_id,uploader_id,filename,sha256,size_bytes,visibility,storage_path,
                   status,embedding_spec,chunk_count,error_message)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                   tuple(d[k] for k in ('id','organization_id','uploader_id','filename','sha256','size_bytes',
                                         'visibility','storage_path','status','embedding_spec','chunk_count','error_message')))
            for c in records['organization_chunks']:
                if c['organization_id']!=org_id:continue
                v=array('f');v.frombytes(c['vector'])
                if len(v)!=c['dimension']:raise ValueError('SQLite 向量损坏，迁移中止')
                conn.execute('''INSERT INTO organization_chunks
                    (id,organization_id,document_id,position,page,content,embedding,dimension)
                    VALUES(%s,%s,%s,%s,%s,%s,%s::vector,%s)''',
                    (c['id'],c['organization_id'],c['document_id'],c['position'],c['page'],c['content'],pg._vector_literal(v),len(v)))
            for j in records['indexing_jobs']:
                if j['organization_id']!=org_id:continue
                # Restart in-progress jobs via the durable queue; old leases are meaningless now.
                state='queued' if j['state']=='running' else j['state']
                conn.execute('''INSERT INTO indexing_jobs
                    (id,organization_id,document_id,state,attempts,max_attempts,worker_id,lease_until,error_message)
                    VALUES(%s,%s,%s,%s,%s,%s,NULL,NULL,%s)''',
                    (j['id'],j['organization_id'],j['document_id'],state,j['attempts'],j['max_attempts'],j['error_message']))
            pg._audit(conn,org_id,None,'migration.import','organization',org_id,
                      details={'source':'sqlite_lesson6','documents':sum(d['organization_id']==org_id for d in records['organization_documents'])})
    print('PostgreSQL 数据导入提交成功；旧 SQLite、旧令牌未修改。')
    return counts


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true',help='真正写入 PG，默认只读预检')
    args=parser.parse_args()
    run(dry_run=not args.execute)

if __name__=='__main__':main()
