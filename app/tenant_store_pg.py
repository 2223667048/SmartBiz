"""PostgreSQL tenant store for /api/v2. Every tenant DML transaction sets local RLS context.

PG_APP_DSN must use smartbiz_app (no BYPASSRLS, not schema owner).
PG_WORKER_DSN uses a separate restricted worker role; never expose it in web container.
"""
import hashlib
import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from . import tenant_store as helpers, kb

TOKEN_TTL = helpers.TOKEN_TTL
LEASE_SECONDS = helpers.LEASE_SECONDS
MAX_TENANT_DOCS = helpers.MAX_TENANT_DOCS
new_id = helpers.new_id
upload_dir = helpers.upload_dir
email_normalized = helpers.email_normalized
validate_password = helpers.validate_password
PASSWORD_HASH = helpers.PASSWORD_HASH
TooManyLoginAttempts = helpers.TooManyLoginAttempts


@contextmanager
def _tx(organization_id=None, role='member', *, worker=False):
    url = os.environ.get('PG_WORKER_DSN' if worker else 'PG_APP_DSN')
    if not url:
        raise RuntimeError('Missing PostgreSQL DSN; see docker-compose.pg.yml')
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as conn:
        if not worker:
            # Never use SET (session); SET LOCAL lives only for this transaction.
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (organization_id or '',))
            conn.execute("SELECT set_config('app.actor_role', %s, true)", (role,))
        yield conn


def migrate():
    # Require a completed explicit Alembic migration before serving traffic.
    if os.environ.get('PG_APP_DSN'):
        with _tx() as conn:
            row=conn.execute("SELECT to_regclass('public.audit_events') AS audit, to_regclass('public.organization_documents') AS docs").fetchone()
            if not row['audit'] or not row['docs']:
                raise RuntimeError('Run alembic upgrade head before starting SmartBiz')
            if os.getenv('GATEWAY_ENABLED','false').lower()=='true':
                ver=conn.execute('SELECT version_num FROM alembic_version').fetchone()
                if not ver or ver['version_num']!='0011_commercial':
                    raise RuntimeError('Schema mismatch: rebuild migrate image, run alembic upgrade head (0011_commercial)')
            role=conn.execute('SELECT current_user AS name, rolbypassrls AS can_bypass, rolsuper AS is_super FROM pg_roles WHERE rolname=current_user').fetchone()
            if role['name']!='smartbiz_app' or role['can_bypass'] or role['is_super']:
                raise RuntimeError('PG_APP_DSN must use smartbiz_app without BYPASSRLS or SUPERUSER')
    else:
        # Worker never needs the web DB credentials; migration is already a Compose dependency.
        with _tx(worker=True) as conn:
            role=conn.execute('SELECT current_user AS name, rolsuper AS is_super, rolbypassrls AS can_bypass FROM pg_roles WHERE rolname=current_user').fetchone()
            if role['name']!='smartbiz_worker' or role['is_super'] or role['can_bypass']:
                raise RuntimeError('PG_WORKER_DSN must use restricted smartbiz_worker role')
            conn.execute('SELECT id FROM indexing_jobs LIMIT 0')


def create_org(name, email, password):
    if not name.strip() or len(name)>100:
        raise ValueError('企业名称不能为空或过长')
    validate_password(password)
    email=email_normalized(email)
    org_id,user_id=new_id(),new_id()
    with _tx() as conn:
        conn.execute('INSERT INTO organizations(id,name) VALUES (%s,%s)', (org_id,name.strip()))
        conn.execute('''INSERT INTO organization_users(id,organization_id,email,password_hash,role)
            VALUES(%s,%s,%s,%s,'admin')''',(user_id,org_id,email,PASSWORD_HASH.hash(password)))
    return {'organization_id':org_id,'user_id':user_id,'email':email}


def create_user(org_id,email,password,role,actor_id=None):
    validate_password(password)
    email=email_normalized(email)
    if role not in ('admin','member'):
        raise ValueError('无效角色')
    uid=new_id()
    try:
        with _tx(org_id,'admin') as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (org_id,))
            entitlement=conn.execute('''SELECT s.status,p.max_seats FROM organization_subscriptions s
              JOIN commercial_plans p ON p.code=s.plan_code WHERE s.organization_id=%s''',(org_id,)).fetchone()
            if not entitlement or entitlement['status'] not in ('trial','active'):
                raise ValueError('企业订阅不可用')
            seat_count=conn.execute('SELECT count(*) AS n FROM organization_users WHERE organization_id=%s AND active=true',(org_id,)).fetchone()['n']
            if seat_count>=entitlement['max_seats']:
                raise ValueError('套餐账号数上限已达到')
            conn.execute('''INSERT INTO organization_users(id,organization_id,email,password_hash,role)
                VALUES(%s,%s,%s,%s,%s)''',(uid,org_id,email,PASSWORD_HASH.hash(password),role))
            _audit(conn,org_id,actor_id,'user.create','user',uid,details={'role':role})
    except errors.UniqueViolation as exc:
        raise ValueError('该邮箱已被使用') from exc
    return {'id':uid,'email':email,'role':role}


def login(email,password,source='local'):
    try: email=email_normalized(email)
    except ValueError: email=''
    key=hashlib.sha256((source+':'+email).encode()).hexdigest()
    now=int(time.time())
    with _tx() as conn:
        attempts=conn.execute('SELECT failures,window_started FROM auth_login_attempts WHERE principal_hash=%s',(key,)).fetchone()
        if attempts and now-attempts['window_started']<300 and attempts['failures']>=8:
            raise TooManyLoginAttempts()
        row=conn.execute('''SELECT u.* FROM organization_users u
          WHERE u.email=%s AND u.active=true AND commercial_subscription_active(u.organization_id)''',(email,)).fetchone()
    dummy='$argon2id$v=19$m=65536,t=3,p=2$QXdCaUkwMGJuNTl0czBqSQ$Xpv6VkpPYcEPtEzz90UPYKDnDGFCqdIxdfXzXoxuGiQ'
    from argon2.exceptions import VerifyMismatchError, VerificationError
    try: verified=PASSWORD_HASH.verify(row['password_hash'] if row else dummy,password)
    except (VerifyMismatchError,VerificationError,ValueError):verified=False
    if not row or not verified:
        with _tx() as conn:
            conn.execute('''INSERT INTO auth_login_attempts(principal_hash,failures,window_started)
              VALUES(%s,1,%s) ON CONFLICT(principal_hash) DO UPDATE SET
              failures=CASE WHEN %s-auth_login_attempts.window_started>=300 THEN 1 ELSE auth_login_attempts.failures+1 END,
              window_started=CASE WHEN %s-auth_login_attempts.window_started>=300 THEN %s ELSE auth_login_attempts.window_started END''',
              (key,now,now,now,now))
        return None
    token=secrets.token_urlsafe(40)
    with _tx(row['organization_id'],'admin') as conn:
        conn.execute('DELETE FROM auth_login_attempts WHERE principal_hash=%s',(key,))
        conn.execute('INSERT INTO organization_tokens(token_hash,user_id,expires_at) VALUES(%s,%s,%s)',
                     (hashlib.sha256(token.encode()).hexdigest(),row['id'],now+TOKEN_TTL))
        _audit(conn,row['organization_id'],row['id'],'auth.login','user',row['id'])
    return {'access_token':token,'token_type':'bearer','expires_in':TOKEN_TTL}


def actor_for_token(token):
    if not token or len(token)>256: return None
    with _tx() as conn:
        row=conn.execute('''SELECT u.id,u.organization_id,u.email,u.role
          FROM organization_tokens t JOIN organization_users u ON u.id=t.user_id
          WHERE t.token_hash=%s AND t.expires_at>%s AND u.active=true
          AND commercial_subscription_active(u.organization_id)''',
          (hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
    return dict(row) if row else None


def logout(token):
    actor=actor_for_token(token)
    with _tx(actor['organization_id'] if actor else None, 'admin' if actor else 'member') as conn:
        conn.execute('DELETE FROM organization_tokens WHERE token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),))
        if actor: _audit(conn,actor['organization_id'],actor['id'],'auth.logout','user',actor['id'])


def list_users(org_id):
    with _tx(org_id,'admin') as conn:
        return list(conn.execute('''SELECT id,email,role,active,created_at FROM organization_users
             WHERE organization_id=%s ORDER BY created_at''',(org_id,)).fetchall())


def _audit(conn, org_id, actor_id, action, target_type, target_id=None, outcome='success', details=None):
    conn.execute('''INSERT INTO audit_events(organization_id,actor_user_id,action,target_type,target_id,outcome,details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)''',
                 (org_id,actor_id,action,target_type,target_id,outcome,json.dumps(details or {})))


def audit_event(actor, action, target_type, target_id=None, details=None):
    with _tx(actor['organization_id'],'admin') as conn:
        _audit(conn,actor['organization_id'],actor['id'],action,target_type,target_id,details=details)


def list_audit(org_id,limit=50):
    with _tx(org_id,'admin') as conn:
        return conn.execute('''SELECT id,occurred_at,actor_user_id,action,target_type,target_id,outcome,details
          FROM audit_events ORDER BY occurred_at DESC,id DESC LIMIT %s''',(limit,)).fetchall()


def enqueue_document(actor,filename,raw,visibility='organization'):
    if visibility not in ('organization','admin'): raise ValueError('无效文档可见范围')
    if not raw or len(raw)>kb.MAX_UPLOAD_BYTES: raise ValueError('文档不能为空且不超过 2 MiB')
    if Path(filename).suffix.lower() not in kb.ALLOWED: raise ValueError('不支持此文件格式')
    filename=filename.replace('\\','/').split('/')[-1][:120]
    doc_id,job_id=new_id(),new_id()
    folder=upload_dir();folder.mkdir(parents=True,exist_ok=True)
    path=folder/(doc_id+'.bin');path.write_bytes(raw)
    try:
        with _tx(actor['organization_id'],'admin') as conn:
            # Serialize upload quota checks for a given tenant.
            conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE',(actor['organization_id'],))
            count=conn.execute("SELECT COUNT(*) AS n FROM organization_documents WHERE organization_id=%s AND status!='deleted'",(actor['organization_id'],)).fetchone()['n']
            if count>=MAX_TENANT_DOCS:raise ValueError('单企业最多 50 份文档')
            conn.execute('''INSERT INTO organization_documents
                (id,organization_id,uploader_id,filename,sha256,size_bytes,storage_path,status,visibility)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s)''',
                (doc_id,actor['organization_id'],actor['id'],filename,hashlib.sha256(raw).hexdigest(),len(raw),str(path),visibility))
            conn.execute("INSERT INTO indexing_jobs(id,organization_id,document_id,state) VALUES(%s,%s,%s,'queued')",
                         (job_id,actor['organization_id'],doc_id))
            _audit(conn,actor['organization_id'],actor['id'],'document.upload','document',doc_id,details={'visibility':visibility})
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'document_id':doc_id,'job_id':job_id,'status':'queued'}


def list_documents(org_id,role):
    with _tx(org_id,role) as conn:
        return conn.execute('''SELECT id,filename,size_bytes,visibility,status,embedding_spec,chunk_count,
           error_message,created_at FROM organization_documents WHERE status!='deleted'
           ORDER BY created_at DESC,id DESC''').fetchall()


def get_document(org_id,doc_id,role):
    with _tx(org_id,role) as conn:
        return conn.execute('''SELECT id,filename,size_bytes,visibility,status,embedding_spec,chunk_count,
           error_message,created_at FROM organization_documents WHERE id=%s AND status!='deleted' ''',(doc_id,)).fetchone()


def delete_document(org_id,doc_id,actor_id=None):
    with _tx(org_id,'admin') as conn:
        doc=conn.execute('''SELECT storage_path FROM organization_documents WHERE id=%s AND status!='deleted' FOR UPDATE''',(doc_id,)).fetchone()
        if not doc:return False
        conn.execute("UPDATE organization_documents SET status='deleted',chunk_count=0 WHERE id=%s",(doc_id,))
        conn.execute('DELETE FROM organization_chunks WHERE document_id=%s',(doc_id,))
        conn.execute('''UPDATE indexing_jobs SET state='canceled',worker_id=NULL,lease_until=NULL,finished_at=now()
                     WHERE document_id=%s AND state IN ('queued','running')''',(doc_id,))
        path=doc['storage_path']
        _audit(conn,org_id,actor_id,'document.delete','document',doc_id)
    Path(path).unlink(missing_ok=True)
    return True


def list_jobs(org_id,role):
    with _tx(org_id,role) as conn:
        return conn.execute('''SELECT id,document_id,state,attempts,max_attempts,error_message,
          created_at,started_at,finished_at FROM indexing_jobs ORDER BY created_at DESC,id DESC''').fetchall()


def get_job(org_id,job_id,role):
    with _tx(org_id,role) as conn:
        return conn.execute('''SELECT id,document_id,state,attempts,max_attempts,error_message,
           created_at,started_at,finished_at FROM indexing_jobs WHERE id=%s''',(job_id,)).fetchone()


def requeue_document(org_id,doc_id,*,reindex=False,actor_id=None):
    with _tx(org_id,'admin') as conn:
        doc=conn.execute('SELECT status,storage_path FROM organization_documents WHERE id=%s FOR UPDATE',(doc_id,)).fetchone()
        if not doc:raise KeyError('文档不存在')
        if doc['status'] not in (('ready','failed') if reindex else ('failed',)):
            raise ValueError('当前状态不允许重新入队')
        if not Path(doc['storage_path']).is_file():raise ValueError('原始文件已经不存在，无法重新处理')
        active=conn.execute("SELECT 1 FROM indexing_jobs WHERE document_id=%s AND state IN ('queued','running') LIMIT 1",(doc_id,)).fetchone()
        if active:raise ValueError('文档已有正在等待或执行的任务')
        job_id=new_id()
        conn.execute("UPDATE organization_documents SET status='queued',error_message=NULL WHERE id=%s",(doc_id,))
        conn.execute("INSERT INTO indexing_jobs(id,organization_id,document_id,state) VALUES(%s,%s,%s,'queued')",(job_id,org_id,doc_id))
        _audit(conn,org_id,actor_id,'document.reindex' if reindex else 'document.retry','document',doc_id)
    return {'job_id':job_id,'document_id':doc_id,'status':'queued'}


def claim_job(worker_id):
    now=int(time.time())
    with _tx(worker=True) as conn:
        conn.execute('''UPDATE indexing_jobs SET state='failed',worker_id=NULL,lease_until=NULL,
            error_message='工作进程多次中断，已停止自动恢复',finished_at=now()
            WHERE state='running' AND lease_until<%s AND attempts>=max_attempts''',(now,))
        conn.execute('''UPDATE organization_documents SET status='failed',error_message='任务超出自动恢复次数'
            WHERE id IN (SELECT document_id FROM indexing_jobs WHERE state='failed'
              AND error_message='工作进程多次中断，已停止自动恢复') AND status='processing' ''')
        job=conn.execute('''SELECT j.id,j.document_id,j.organization_id,j.attempts,j.max_attempts,
           d.filename,d.storage_path FROM indexing_jobs j
           JOIN organization_documents d ON d.id=j.document_id AND d.organization_id=j.organization_id
           WHERE (j.state='queued' OR (j.state='running' AND j.lease_until<%s))
           AND j.attempts<j.max_attempts AND d.status IN ('queued','processing')
           ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''',(now,)).fetchone()
        if not job:return None
        conn.execute('''UPDATE indexing_jobs SET state='running',attempts=attempts+1,
            worker_id=%s,lease_until=%s,started_at=now(),error_message=NULL WHERE id=%s''',
            (worker_id,now+LEASE_SECONDS,job['id']))
        conn.execute("UPDATE organization_documents SET status='processing',error_message=NULL WHERE id=%s",(job['document_id'],))
        return job


def heartbeat(job_id,worker_id):
    now=int(time.time())
    with _tx(worker=True) as conn:
        cur=conn.execute('''UPDATE indexing_jobs SET lease_until=%s
           WHERE id=%s AND state='running' AND worker_id=%s AND lease_until>=%s''',
           (now+LEASE_SECONDS,job_id,worker_id,now))
        return cur.rowcount==1


def _vector_literal(vector):
    from math import isfinite
    values=list(vector)
    if not values or len(values)>2000 or not all(isfinite(float(x)) for x in values):
        raise ValueError('pgvector 仅支持 1-2000 维有限数值向量')
    return '['+','.join(str(float(x)) for x in values)+']'


def complete_job(job,worker_id,spec,chunks,vectors):
    if not chunks or len(chunks)!=len(vectors) or len({len(v) for v in vectors})!=1:
        raise ValueError('文本块和向量不匹配')
    # Fail before entering transaction if vector length exceeds pgvector limits.
    serialized=[_vector_literal(v) for v in vectors]
    with _tx(worker=True) as conn:
        valid=conn.execute('''SELECT 1 FROM indexing_jobs j JOIN organization_documents d
          ON d.id=j.document_id AND d.organization_id=j.organization_id
          WHERE j.id=%s AND j.worker_id=%s AND j.state='running' AND j.lease_until>%s
            AND d.status='processing' AND d.organization_id=%s FOR UPDATE OF j''',
          (job['id'],worker_id,int(time.time()),job['organization_id'])).fetchone()
        if not valid:raise RuntimeError('任务租约已失效或文件已删除，不提交结果')
        conn.execute('DELETE FROM organization_chunks WHERE organization_id=%s AND document_id=%s',
                    (job['organization_id'],job['document_id']))
        for part,vec in zip(chunks,serialized):
            conn.execute('''INSERT INTO organization_chunks(id,organization_id,document_id,position,page,content,embedding,dimension)
              VALUES(%s,%s,%s,%s,%s,%s,%s::vector,%s)''',
              (new_id(),job['organization_id'],job['document_id'],part['position'],part['page'],part['content'],vec,len(vectors[0])))
        conn.execute('''UPDATE organization_documents SET status='ready',chunk_count=%s,embedding_spec=%s,error_message=NULL
                      WHERE id=%s AND organization_id=%s''',(len(chunks),spec,job['document_id'],job['organization_id']))
        conn.execute('''UPDATE indexing_jobs SET state='succeeded',lease_until=NULL,worker_id=NULL,
                      error_message=NULL,finished_at=now() WHERE id=%s''',(job['id'],))
        _audit(conn,job['organization_id'],None,'index.succeeded','job',job['id'],details={'chunks':len(chunks)})


def fail_job(job,worker_id,reason):
    with _tx(worker=True) as conn:
        current=conn.execute("SELECT id FROM indexing_jobs WHERE id=%s AND worker_id=%s AND state='running' FOR UPDATE",
                             (job['id'],worker_id)).fetchone()
        if not current:return
        conn.execute('''UPDATE indexing_jobs SET state='failed',error_message=%s,worker_id=NULL,lease_until=NULL,
                     finished_at=now() WHERE id=%s''',(reason[:300],job['id']))
        conn.execute('''UPDATE organization_documents SET status='failed',error_message=%s
             WHERE id=%s AND organization_id=%s AND status='processing' ''',(reason[:300],job['document_id'],job['organization_id']))
        _audit(conn,job['organization_id'],None,'index.failed','job',job['id'],outcome='failure')


def visible_specs(org_id,role):
    with _tx(org_id,role) as conn:
        return {r['embedding_spec'] for r in conn.execute("SELECT DISTINCT embedding_spec FROM organization_documents WHERE status='ready'").fetchall()}


def search(organization_id,role,vector,spec,limit=3,min_score=0.25):
    literal=_vector_literal(vector)
    with _tx(organization_id,role) as conn:
        specs={r['embedding_spec'] for r in conn.execute("SELECT DISTINCT embedding_spec FROM organization_documents WHERE status='ready'")}
        if specs and specs!={spec}:
            raise ValueError('本企业存在旧模型索引，请先重建文档向量')
        # HNSW expression index can accelerate 1536d search; other dimensions use exact pgvector search.
        distance = '(c.embedding::vector(1536) <=> %s::vector(1536))' if len(vector)==1536 else '(c.embedding <=> %s::vector)'
        sql=f'''SELECT c.id AS chunk_id,c.document_id,c.position,c.page,c.content AS excerpt,
             d.filename,1-{distance} AS similarity
           FROM organization_chunks c JOIN organization_documents d ON d.id=c.document_id AND d.organization_id=c.organization_id
           WHERE d.status='ready' AND d.embedding_spec=%s AND c.dimension=%s
           ORDER BY {distance} LIMIT %s'''
        rows=conn.execute(sql,(literal,spec,len(vector),literal,limit)).fetchall()
    matches=[]
    for r in rows:
        score=float(r['similarity'])
        if score>=min_score:
            obj=dict(r);obj['similarity']=round(score,4);matches.append(obj)
    for i, item in enumerate(matches,1):item['citation']=f'[{i}]'
    return matches


# Tenant security: revoke user and all outstanding tokens within one transaction.
def disable_user(organization_id: str, user_id: str, actor_id: str) -> bool:
    if user_id == actor_id:
        raise ValueError('不能停用当前管理员账号')
    with _tx(organization_id,'admin') as conn:
        conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE',(organization_id,))
        row=conn.execute('''SELECT role,active FROM organization_users
            WHERE id=%s AND organization_id=%s FOR UPDATE''',(user_id,organization_id)).fetchone()
        if not row: return False
        if not row['active']: return True
        if row['role']=='admin':
            n=conn.execute('''SELECT COUNT(*) AS n FROM organization_users
               WHERE organization_id=%s AND role='admin' AND active=true''',(organization_id,)).fetchone()['n']
            if n<=1: raise ValueError('不能停用企业最后一个有效管理员')
        conn.execute('UPDATE organization_users SET active=false WHERE id=%s AND organization_id=%s',
                     (user_id,organization_id))
        conn.execute('DELETE FROM organization_tokens WHERE user_id=%s',(user_id,))
        _audit(conn,organization_id,actor_id,'user.disable','user',user_id)
    return True


def consume_rate_limit(org_id: str, scope: str, limit: int, now: int | None = None) -> int:
    if scope not in ('ask','search','upload') or not 1<=limit<=1000:
        raise ValueError('无效限流参数')
    now=int(time.time()) if now is None else int(now)
    window=(now//60)*60
    with _tx(org_id,'member') as conn:
        # Tenant-constrained seven-day cleanup; PG RLS remains active here.
        conn.execute('DELETE FROM tenant_rate_limits WHERE organization_id=%s AND window_start<%s',
                     (org_id,window-7*86400))
        # Atomic ON CONFLICT increments serialize concurrent replicas.
        row=conn.execute('''INSERT INTO tenant_rate_limits(organization_id,scope,window_start,hits)
          VALUES(%s,%s,%s,1) ON CONFLICT(organization_id,scope,window_start)
          DO UPDATE SET hits=tenant_rate_limits.hits+1 RETURNING hits''',
          (org_id,scope,window)).fetchone()
    return 60-(now-window) if row['hits']>limit else 0


def job_metrics(org_id: str) -> dict:
    with _tx(org_id,'admin') as conn:
        grouped=conn.execute('''SELECT state,COUNT(*) AS n FROM indexing_jobs
             WHERE organization_id=%s GROUP BY state''',(org_id,)).fetchall()
        aged=conn.execute('''SELECT EXTRACT(EPOCH FROM (now()-MIN(created_at))) AS age
          FROM indexing_jobs WHERE organization_id=%s AND state='queued' ''',(org_id,)).fetchone()['age']
        stale=conn.execute('''SELECT COUNT(*) AS n FROM indexing_jobs WHERE organization_id=%s
          AND state='running' AND lease_until<EXTRACT(EPOCH FROM now())''',(org_id,)).fetchone()['n']
    return {'counts':{r['state']:r['n'] for r in grouped},
            'oldest_queued_seconds':max(0,int(aged)) if aged is not None else 0,
            'stale_leases':stale}

def audit_summary(org_id: str) -> dict:
    """Tenant-scoped counts; no raw documents or user prompts returned."""
    with _tx(org_id, 'admin') as conn:
        rows = conn.execute("""SELECT action, outcome, count(*) AS n FROM audit_events
            WHERE organization_id=%s AND occurred_at >= now()-interval '24 hours'
            GROUP BY action,outcome ORDER BY action,outcome""", (org_id,)).fetchall()
    return {'period_hours': 24, 'events': [
        {'action': r['action'], 'outcome': r['outcome'], 'count': r['n']} for r in rows]}


def reserve_model_estimate(org_id: str, daily_cap_micro: int, per_call_micro: int) -> bool:
    """Atomic conservative budget reservation. No refunds: failures cost provider time too.

    Not actual billed tokens/USD. Provider-side spending limits are mandatory.
    """
    if not 0 < per_call_micro <= daily_cap_micro <= 10**12:
        raise ValueError('无效的预算配置')
    with _tx(org_id, 'member') as conn:
        row = conn.execute('''INSERT INTO tenant_model_estimates
                (organization_id,utc_day,reserved_micro_usd,requests)
                VALUES(%s,(now() at time zone 'utc')::date,%s,1)
                ON CONFLICT (organization_id,utc_day) DO UPDATE
                   SET reserved_micro_usd=tenant_model_estimates.reserved_micro_usd+EXCLUDED.reserved_micro_usd,
                       requests=tenant_model_estimates.requests+1
                WHERE tenant_model_estimates.reserved_micro_usd+EXCLUDED.reserved_micro_usd <= %s
                RETURNING reserved_micro_usd''',
                (org_id,per_call_micro,daily_cap_micro)).fetchone()
        return row is not None


def worker_heartbeat(worker_id: str):
    """Dedicated worker role; do not expose this credential in the web container."""
    with _tx(worker=True) as conn:
        conn.execute('''INSERT INTO worker_heartbeats(worker_id,seen_at)
          VALUES (%s,now()) ON CONFLICT(worker_id) DO UPDATE SET seen_at=now()''', (worker_id,))
