"""INTERNAL operations metrics, restricted worker role, aggregate only, no tenant labels.

A tiny single-process server. Never expose port 9105 through Docker to host.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import time
import logging
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

from .tenant_store_pg import _tx

QUEUE = Gauge('smartbiz_index_jobs','Job count', ['state'])
AGE = Gauge('smartbiz_queue_oldest_age_seconds','Age of oldest queued job')
WORKER_AGE = Gauge('smartbiz_worker_last_seen_age_seconds','Time since latest worker heartbeat')
BUDGET = Gauge('smartbiz_model_budget_max_reserved_ratio','Maximum estimated reserved budget ratio across tenants')


def collect():
    with _tx(worker=True) as conn:
        states = conn.execute("SELECT state,count(*) AS n FROM indexing_jobs GROUP BY state").fetchall()
        row = conn.execute("SELECT EXTRACT(EPOCH FROM(now()-MIN(created_at))) AS age FROM indexing_jobs WHERE state='queued'").fetchone()
        last = conn.execute("SELECT EXTRACT(EPOCH FROM(now()-max(seen_at))) AS age FROM worker_heartbeats").fetchone()
        reserved = conn.execute('''SELECT COALESCE(max(reserved_micro_usd),0) AS max_used
           FROM tenant_model_estimates WHERE utc_day=(now() at time zone 'utc')::date''').fetchone()
    found = {r['state']:r['n'] for r in states}
    for name in ('queued','running','succeeded','failed','canceled'):
        QUEUE.labels(name).set(found.get(name,0))
    AGE.set(float(row['age'] or 0))
    # No heartbeat is concerning as soon as worker starts; age > alert threshold.
    WORKER_AGE.set(float(last['age']) if last['age'] is not None else 99999)
    from .cost_budget import amounts
    cap,_=amounts()
    BUDGET.set(min(10,float(reserved['max_used'])/cap))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/metrics':
            self.send_error(404);return
        try:
            collect()
            body = generate_latest()
            self.send_response(200)
            self.send_header('Content-Type',CONTENT_TYPE_LATEST)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            logging.exception('metrics collection failed')
            self.send_error(503)

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    ThreadingHTTPServer(('0.0.0.0',9105),Handler).serve_forever()
