"""Durable indexing worker. Start separately: python -m app.tenant_worker.

SQLite atomic claims + lease heartbeat support process restarts and prevent stale writes.
"""
import argparse
import asyncio
import logging
from pathlib import Path
import threading
import time
import uuid
from . import kb, embeddings, db
from .tenant_backend import store

log = logging.getLogger('smartbiz.worker')


def process_one(worker_id: str | None = None) -> bool:
    store.migrate()
    worker_id = worker_id or str(uuid.uuid4())
    job = store.claim_job(worker_id)
    if job is None:
        return False
    stop = threading.Event()
    def keepalive():
        while not stop.wait(30):
            if not store.heartbeat(job['id'], worker_id):
                break
    thread = threading.Thread(target=keepalive, daemon=True)
    thread.start()
    try:
        raw = Path(job['storage_path']).read_bytes()
        if len(raw) > kb.MAX_UPLOAD_BYTES:
            raise kb.KbInputError('文档体积超过限制')
        chunks = kb.chunk_sections(kb.extract_sections(job['filename'], raw))
        spec = embeddings.index_spec()
        vectors = asyncio.run(embeddings.embed_many([x['content'] for x in chunks]))
        store.complete_job(job, worker_id, spec, chunks, vectors)
        log.info('index succeeded job=%s chunks=%d', job['id'], len(chunks))
    except Exception as exc:
        log.warning('index failed job=%s cause=%s', job['id'], type(exc).__name__)
        store.fail_job(job, worker_id, '索引失败：' + type(exc).__name__)
    finally:
        stop.set()
        thread.join(timeout=2)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='process at most one job')
    parser.add_argument('--poll', type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    db.initialize()
    store.migrate()
    wid = str(uuid.uuid4())
    if args.once:
        process_one(wid)
        return
    while True:
        try:
            if hasattr(store, "worker_heartbeat"):
                store.worker_heartbeat(wid)
            if not process_one(wid):
                time.sleep(max(0.5,args.poll))
        except Exception:
            log.exception('worker loop failed')
            time.sleep(3)

if __name__ == '__main__':
    main()
