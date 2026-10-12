"""Tenant platform: auditable tenant-scoped DB operations and durable SQLite work queue."""
from array import array
import hashlib
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from . import db, embeddings, kb
from .settings import PROJECT_ROOT

PASSWORD_HASH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
TOKEN_TTL = 8 * 3600
LEASE_SECONDS = 900
MAX_TENANT_DOCS = 50


def new_id():
    return str(uuid.uuid4())


def upload_dir() -> Path:
    import os
    dest = Path(os.getenv('SMARTBIZ_UPLOAD_DIR', './data/tenant-uploads'))
    return dest if dest.is_absolute() else PROJECT_ROOT / dest


def migrate():
    sql = (Path(__file__).parent / 'migrations' / '0006_tenants.sql').read_text(encoding='utf-8')
    with db.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=6').fetchone():
            # CREATE IF NOT EXISTS means rerunning after interrupted DDL is safe.
            conn.executescript(sql)
            conn.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(6)')
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=8').fetchone():
            upgrade = (Path(__file__).parent / 'migrations' / '0008_security.sql').read_text(encoding='utf-8')
            conn.executescript(upgrade)
            conn.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(8)')


def validate_password(password: str):
    if len(password) < 12 or len(password) > 256:
        raise ValueError('密码长度必须为 12~256 字符')
    if len(password.encode('utf-8')) > 1024:
        raise ValueError('密码过长')


def email_normalized(email: str):
    email = email.strip().lower()
    if len(email) > 254 or '@' not in email or ' ' in email:
        raise ValueError('邮箱格式错误')
    return email


def create_org(name: str, email: str, password: str) -> dict:
    if not name.strip() or len(name) > 100:
        raise ValueError('企业名称不能为空或过长')
    validate_password(password)
    email = email_normalized(email)
    oid, uid = new_id(), new_id()
    digest = PASSWORD_HASH.hash(password)
    with db.connection() as conn:
        conn.execute('INSERT INTO organizations(id,name) VALUES (?,?)', (oid, name.strip()))
        conn.execute('INSERT INTO organization_users(id,organization_id,email,password_hash,role) VALUES (?,?,?,?,?)',
                     (uid, oid, email, digest, 'admin'))
    return {'organization_id': oid, 'user_id': uid, 'email': email}


def create_user(org_id: str, email: str, password: str, role: str, actor_id=None) -> dict:
    validate_password(password)
    email = email_normalized(email)
    if role not in ('member','admin'):
        raise ValueError('无效角色')
    uid = new_id()
    try:
        with db.connection() as conn:
            conn.execute('INSERT INTO organization_users(id,organization_id,email,password_hash,role) VALUES (?,?,?,?,?)',
                         (uid, org_id, email, PASSWORD_HASH.hash(password), role))
    except sqlite3.IntegrityError as exc:
        raise ValueError('该邮箱已被使用') from exc
    return {'id': uid, 'email': email, 'role': role}


class TooManyLoginAttempts(Exception):
    pass


def _attempt_key(email: str, source: str) -> str:
    return hashlib.sha256((source + ':' + email).encode('utf8')).hexdigest()


def login(email: str, password: str, source: str = 'local') -> dict | None:
    try:
        email = email_normalized(email)
    except ValueError:
        email = ''
    key = _attempt_key(email, source)
    now = int(time.time())
    with db.connection() as conn:
        attempts = conn.execute('SELECT failures,window_started FROM auth_login_attempts WHERE principal_hash=?',(key,)).fetchone()
        if attempts and now - attempts['window_started'] < 300 and attempts['failures'] >= 8:
            raise TooManyLoginAttempts()
        row = conn.execute('SELECT * FROM organization_users WHERE email=? AND active=1', (email,)).fetchone()
    # Dummy verification reduces difference between nonexistent and existing users.
    dummy = '$argon2id$v=19$m=65536,t=3,p=2$QXdCaUkwMGJuNTl0czBqSQ$Xpv6VkpPYcEPtEzz90UPYKDnDGFCqdIxdfXzXoxuGiQ'
    try:
        verified = PASSWORD_HASH.verify(row['password_hash'] if row else dummy, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        verified = False
    if not row or not verified:
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''INSERT INTO auth_login_attempts(principal_hash,failures,window_started) VALUES(?,1,?)
                ON CONFLICT(principal_hash) DO UPDATE SET
                  failures=CASE WHEN ?-window_started>=300 THEN 1 ELSE failures+1 END,
                  window_started=CASE WHEN ?-window_started>=300 THEN ? ELSE window_started END''',
                  (key,now,now,now,now))
        return None
    with db.connection() as conn:
        conn.execute('DELETE FROM auth_login_attempts WHERE principal_hash=?',(key,))
    token = secrets.token_urlsafe(40)
    now = int(time.time())
    with db.connection() as conn:
        conn.execute('INSERT INTO organization_tokens(token_hash,user_id,expires_at) VALUES(?,?,?)',
                     (hashlib.sha256(token.encode()).hexdigest(), row['id'], now + TOKEN_TTL))
    return {'access_token': token, 'token_type': 'bearer', 'expires_in': TOKEN_TTL}


def actor_for_token(token: str | None) -> dict | None:
    if not token or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db.connection() as conn:
        row = conn.execute('''SELECT u.id, u.organization_id, u.email, u.role
          FROM organization_tokens t JOIN organization_users u ON u.id=t.user_id
          WHERE t.token_hash=? AND t.expires_at>? AND u.active=1''', (digest, int(time.time()))).fetchone()
    return dict(row) if row else None


def logout(token: str):
    with db.connection() as conn:
        conn.execute('DELETE FROM organization_tokens WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


def list_users(org_id: str):
    with db.connection() as conn:
        rows = conn.execute('SELECT id,email,role,active,created_at FROM organization_users WHERE organization_id=? ORDER BY created_at', (org_id,)).fetchall()
    return [dict(r) for r in rows]


def enqueue_document(actor: dict, filename: str, raw: bytes, visibility: str='organization') -> dict:
    if visibility not in ('organization','admin'):
        raise ValueError('无效文档可见范围')
    if not raw or len(raw) > kb.MAX_UPLOAD_BYTES:
        raise ValueError('文档不能为空且不超过 2 MiB')
    if Path(filename).suffix.lower() not in kb.ALLOWED:
        raise ValueError('不支持此文件格式')
    name = filename.replace('\\', '/').split('/')[-1][:120]
    docid, jobid = new_id(), new_id()
    digest = hashlib.sha256(raw).hexdigest()
    folder = upload_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (docid + '.bin')
    path.write_bytes(raw)  # random server-generated filename; no untrusted path segments
    try:
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count = conn.execute('''SELECT COUNT(*) FROM organization_documents
                WHERE organization_id=? AND status!='deleted' ''', (actor['organization_id'],)).fetchone()[0]
            if count >= MAX_TENANT_DOCS:
                raise ValueError('单企业最多 50 份文档')
            conn.execute('''INSERT INTO organization_documents
                 (id,organization_id,uploader_id,filename,sha256,size_bytes,storage_path,status,visibility)
                 VALUES(?,?,?,?,?,?,?,?,?)''',
                 (docid,actor['organization_id'],actor['id'],name,digest,len(raw),str(path),'queued',visibility))
            conn.execute('''INSERT INTO indexing_jobs(id,organization_id,document_id,state)
                            VALUES(?,?,?,'queued')''', (jobid,actor['organization_id'],docid))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'document_id': docid, 'job_id': jobid, 'status': 'queued'}


def list_documents(org_id: str, role: str):
    with db.connection() as conn:
        rows = conn.execute('''SELECT id,filename,size_bytes,visibility,status,embedding_spec,chunk_count,error_message,created_at
          FROM organization_documents WHERE organization_id=? AND status!='deleted'
          AND (visibility='organization' OR ?='admin') ORDER BY created_at DESC,id DESC''', (org_id,role)).fetchall()
    return [dict(r) for r in rows]


def get_document(org_id: str, doc_id: str, role: str) -> dict | None:
    with db.connection() as conn:
        r = conn.execute('''SELECT id,filename,size_bytes,visibility,status,embedding_spec,chunk_count,error_message,created_at
              FROM organization_documents WHERE organization_id=? AND id=? AND status!='deleted'
              AND (visibility='organization' OR ?='admin') ''',(org_id,doc_id,role)).fetchone()
    return dict(r) if r else None


def delete_document(org_id: str, doc_id: str, actor_id=None) -> bool:
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        doc = conn.execute('SELECT storage_path FROM organization_documents WHERE organization_id=? AND id=? AND status!=\'deleted\'',
                           (org_id,doc_id)).fetchone()
        if not doc:
            return False
        conn.execute('UPDATE organization_documents SET status=\'deleted\',chunk_count=0 WHERE organization_id=? AND id=?', (org_id,doc_id))
        conn.execute('DELETE FROM organization_chunks WHERE organization_id=? AND document_id=?', (org_id,doc_id))
        conn.execute('''UPDATE indexing_jobs SET state='canceled',worker_id=NULL,lease_until=NULL,finished_at=CURRENT_TIMESTAMP
           WHERE organization_id=? AND document_id=? AND state IN ('queued','running')''', (org_id,doc_id))
        path = doc['storage_path']
    # Delete outside transaction; paths are generated by us, not supplied by clients.
    Path(path).unlink(missing_ok=True)
    return True


def list_jobs(org_id: str, role: str):
    with db.connection() as conn:
        rows = conn.execute('''SELECT id,document_id,state,attempts,max_attempts,error_message,created_at,started_at,finished_at
            FROM indexing_jobs WHERE organization_id=? AND (?='admin' OR document_id IN
             (SELECT id FROM organization_documents WHERE organization_id=? AND visibility='organization'))
             ORDER BY created_at DESC,id DESC LIMIT 100''',(org_id,role,org_id)).fetchall()
    return [dict(r) for r in rows]


def get_job(org_id: str, job_id: str, role: str):
    with db.connection() as conn:
        row = conn.execute('''SELECT id,document_id,state,attempts,max_attempts,error_message,created_at,started_at,finished_at
            FROM indexing_jobs WHERE organization_id=? AND id=? AND (?='admin' OR document_id IN
             (SELECT id FROM organization_documents WHERE organization_id=? AND visibility='organization'))''',(org_id,job_id,role,org_id)).fetchone()
    return dict(row) if row else None


def requeue_document(org_id: str, doc_id: str, *, reindex=False, actor_id=None) -> dict:
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        doc = conn.execute('''SELECT status,storage_path FROM organization_documents
                WHERE organization_id=? AND id=?''',(org_id,doc_id)).fetchone()
        if not doc:
            raise KeyError('文档不存在')
        if doc['status'] not in (('ready','failed') if reindex else ('failed',)):
            raise ValueError('当前状态不允许重新入队')
        if not Path(doc['storage_path']).is_file():
            raise ValueError('原始文件已经不存在，无法重新处理')
        active = conn.execute('''SELECT 1 FROM indexing_jobs WHERE organization_id=? AND document_id=?
             AND state IN ('queued','running') LIMIT 1''',(org_id,doc_id)).fetchone()
        if active:
            raise ValueError('文档已有正在等待或执行的任务')
        jobid = new_id()
        conn.execute('UPDATE organization_documents SET status=\'queued\',error_message=NULL WHERE organization_id=? AND id=?', (org_id,doc_id))
        conn.execute('INSERT INTO indexing_jobs(id,organization_id,document_id,state) VALUES(?,?,?,\'queued\')',
                     (jobid,org_id,doc_id))
    return {'job_id': jobid, 'document_id':doc_id,'status':'queued'}


def claim_job(worker_id: str) -> dict | None:
    now = int(time.time())
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("""UPDATE indexing_jobs SET state='failed',worker_id=NULL,lease_until=NULL,
                 error_message='工作进程多次中断，已停止自动恢复',finished_at=CURRENT_TIMESTAMP
                 WHERE state='running' AND lease_until<? AND attempts>=max_attempts""",(now,))
        conn.execute("""UPDATE organization_documents SET status='failed',error_message='任务超出自动恢复次数'
                 WHERE id IN (SELECT document_id FROM indexing_jobs WHERE state='failed'
                   AND error_message='工作进程多次中断，已停止自动恢复') AND status='processing'""")
        r = conn.execute('''SELECT j.id,j.document_id,j.organization_id,j.attempts,j.max_attempts,
               d.filename,d.storage_path FROM indexing_jobs j
            JOIN organization_documents d ON d.id=j.document_id AND d.organization_id=j.organization_id
            WHERE ((j.state='queued') OR (j.state='running' AND j.lease_until<?))
              AND j.attempts<j.max_attempts AND d.status IN ('queued','processing')
            ORDER BY j.created_at,j.id LIMIT 1''',(now,)).fetchone()
        if not r:
            return None
        updated = conn.execute('''UPDATE indexing_jobs SET state='running', attempts=attempts+1,
           worker_id=?,lease_until=?,started_at=CURRENT_TIMESTAMP,error_message=NULL WHERE id=?''',
           (worker_id,now+LEASE_SECONDS,r['id']))
        if updated.rowcount != 1:
            return None
        conn.execute('UPDATE organization_documents SET status=\'processing\',error_message=NULL WHERE id=? AND organization_id=?',
                     (r['document_id'],r['organization_id']))
        return dict(r)


def heartbeat(job_id: str, worker_id: str):
    with db.connection() as conn:
        c = conn.execute('''UPDATE indexing_jobs SET lease_until=? WHERE id=? AND state='running' AND worker_id=? AND lease_until>=?''',
                         (int(time.time())+LEASE_SECONDS,job_id,worker_id,int(time.time())))
    return c.rowcount == 1


def complete_job(job: dict, worker_id: str, spec: str, chunks: list[dict], vectors: list[list[float]]):
    if not chunks or len(chunks) != len(vectors) or len({len(v) for v in vectors}) != 1:
        raise ValueError('文本块和向量不匹配')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        valid = conn.execute('''SELECT 1 FROM indexing_jobs j
          JOIN organization_documents d ON d.id=j.document_id AND d.organization_id=j.organization_id
          WHERE j.id=? AND j.worker_id=? AND j.state='running' AND j.lease_until>?
            AND d.status='processing' AND d.organization_id=?''',
          (job['id'],worker_id,int(time.time()),job['organization_id'])).fetchone()
        if not valid:
            raise RuntimeError('任务租约已失效或文件已删除，不提交结果')
        conn.execute('DELETE FROM organization_chunks WHERE organization_id=? AND document_id=?',
                     (job['organization_id'],job['document_id']))
        for part, v in zip(chunks,vectors):
            conn.execute('''INSERT INTO organization_chunks(id,organization_id,document_id,position,page,content,vector,dimension)
                VALUES (?,?,?,?,?,?,?,?)''', (new_id(),job['organization_id'],job['document_id'],part['position'],part['page'],part['content'],array('f',v).tobytes(),len(v)))
        conn.execute('''UPDATE organization_documents SET status='ready',chunk_count=?,embedding_spec=?,error_message=NULL
            WHERE id=? AND organization_id=?''', (len(chunks),spec,job['document_id'],job['organization_id']))
        conn.execute('''UPDATE indexing_jobs SET state='succeeded',lease_until=NULL,worker_id=NULL,
            error_message=NULL,finished_at=CURRENT_TIMESTAMP WHERE id=?''', (job['id'],))


def fail_job(job: dict, worker_id: str, reason: str):
    reason = reason[:300]
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        r = conn.execute("SELECT attempts,max_attempts FROM indexing_jobs WHERE id=? AND worker_id=? AND state='running'",
                         (job['id'],worker_id)).fetchone()
        if not r:
            return
        # No automatic immediate retries of permanent invalid files; administrator may retry failed jobs.
        conn.execute('''UPDATE indexing_jobs SET state='failed',error_message=?,worker_id=NULL,lease_until=NULL,
                finished_at=CURRENT_TIMESTAMP WHERE id=?''', (reason,job['id']))
        conn.execute('''UPDATE organization_documents SET status='failed',error_message=?
                WHERE id=? AND organization_id=? AND status='processing' ''',
                     (reason,job['document_id'],job['organization_id']))


def search(organization_id: str, role: str, vector: list[float], spec: str, limit: int=3, min_score: float=0.25):
    with db.connection() as conn:
        specs = {r[0] for r in conn.execute('''SELECT DISTINCT embedding_spec FROM organization_documents
                WHERE organization_id=? AND status='ready' AND (visibility='organization' OR ?='admin')''',(organization_id,role)).fetchall()}
        if specs and specs != {spec}:
            raise ValueError('本企业存在旧模型索引，请先重建文档向量')
        rows = conn.execute('''SELECT c.id,c.document_id,c.position,c.page,c.content,c.vector,c.dimension,d.filename
                FROM organization_chunks c JOIN organization_documents d ON d.id=c.document_id
                  AND d.organization_id=c.organization_id
                WHERE c.organization_id=? AND d.status='ready' AND d.embedding_spec=? AND (d.visibility='organization' OR ?='admin')''',
                (organization_id,spec,role)).fetchall()
    import math
    matches=[]
    for r in rows:
        if r['dimension'] != len(vector):
            raise ValueError('向量维度不一致，请重建索引')
        v = array('f'); v.frombytes(r['vector'])
        score = sum(a*b for a,b in zip(v,vector))
        if math.isfinite(score) and score >= min_score:
            matches.append({'chunk_id':r['id'],'document_id':r['document_id'],
                            'filename':r['filename'],'page':r['page'],'position':r['position'],
                            'excerpt':r['content'],'similarity':round(score,4)})
    matches.sort(key=lambda x:(-x['similarity'],x['filename'],x['position']))
    output=matches[:limit]
    for i,row in enumerate(output,1):
        row['citation']=f'[{i}]'
    return output


def visible_specs(organization_id: str, role: str) -> set[str]:
    """Only embedding models belonging to documents this actor may see."""
    with db.connection() as conn:
        return {row[0] for row in conn.execute('''SELECT DISTINCT embedding_spec FROM organization_documents
          WHERE organization_id=? AND status='ready' AND (visibility='organization' OR ?='admin')''',
          (organization_id, role)).fetchall()}


def audit_event(actor, action, target_type, target_id=None, details=None):
    """SQLite legacy backend has no enterprise audit; use PostgreSQL mode."""
    return None

def list_audit(org_id, limit=50):
    raise RuntimeError('企业审计日志只在 PostgreSQL 后端实现')


# Tenant security: all tenant security checks are enforced by the server, not UI controls.
def disable_user(organization_id: str, user_id: str, actor_id: str) -> bool:
    if user_id == actor_id:
        raise ValueError('不能停用当前管理员账号')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT role,active FROM organization_users WHERE organization_id=? AND id=?',
                           (organization_id,user_id)).fetchone()
        if row is None:
            return False
        if not row['active']:
            return True
        if row['role']=='admin':
            n=conn.execute("SELECT COUNT(*) FROM organization_users WHERE organization_id=? AND role='admin' AND active=1",
                           (organization_id,)).fetchone()[0]
            if n<=1:
                raise ValueError('不能停用企业最后一个有效管理员')
        conn.execute('UPDATE organization_users SET active=0 WHERE organization_id=? AND id=?',
                     (organization_id,user_id))
        conn.execute('DELETE FROM organization_tokens WHERE user_id=?',(user_id,))
    return True


def consume_rate_limit(org_id: str, scope: str, limit: int, now: int | None = None) -> int:
    # Atomic tenant-wide fixed-minute window; returns Retry-After or zero.
    if scope not in ('ask','search','upload') or not 1 <= limit <= 1000:
        raise ValueError('无效限流参数')
    now = int(time.time()) if now is None else int(now)
    window = (now // 60)*60
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Opportunistic 7-day cleanup, constrained to this tenant.
        conn.execute('DELETE FROM tenant_rate_limits WHERE organization_id=? AND window_start<?',
                     (org_id, window-7*86400))
        # Atomic upsert: all callers serialize on SQLite's writer lock.
        conn.execute('''INSERT INTO tenant_rate_limits(organization_id,scope,window_start,hits)
          VALUES(?,?,?,1) ON CONFLICT(organization_id,scope,window_start)
          DO UPDATE SET hits=hits+1''',(org_id,scope,window))
        hits=conn.execute('''SELECT hits FROM tenant_rate_limits
          WHERE organization_id=? AND scope=? AND window_start=?''',(org_id,scope,window)).fetchone()[0]
    return 60-(now-window) if hits>limit else 0


def job_metrics(org_id: str) -> dict:
    # Tenant-local aggregate; admin only at API boundary.
    now=int(time.time())
    with db.connection() as conn:
        groups=conn.execute('''SELECT state,COUNT(*) AS n FROM indexing_jobs
            WHERE organization_id=? GROUP BY state''',(org_id,)).fetchall()
        waiting=conn.execute('''SELECT MIN(CAST(strftime('%s',created_at) AS INTEGER)) AS earliest
             FROM indexing_jobs WHERE organization_id=? AND state='queued' ''',(org_id,)).fetchone()['earliest']
        stale=conn.execute('''SELECT COUNT(*) FROM indexing_jobs WHERE organization_id=?
             AND state='running' AND lease_until<?''',(org_id,now)).fetchone()[0]
    return {'counts':{row['state']:row['n'] for row in groups},
            'oldest_queued_seconds':max(0,now-waiting) if waiting is not None else 0,
            'stale_leases':stale}
