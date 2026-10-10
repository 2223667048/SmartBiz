"""Small single-worker Prometheus registry, private route, JSON access log.

Never include prompts, tokens, raw URLs, user IDs or tenant IDs as metric labels.
"""
import json
import logging
import secrets
import time
import uuid
import os

from fastapi import HTTPException, Request
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

_requests = Counter('smartbiz_http_requests_total','HTTP requests', ['route','method','status'])
_latency = Histogram('smartbiz_http_request_duration_seconds','HTTP request duration',['route','method'],buckets=(.01,.05,.1,.25,.5,1,2,5,10,30))
_log = logging.getLogger('smartbiz.access')


def _route(request):
    route = request.scope.get('route')
    path = getattr(route, 'path', None)
    # Do not label on raw paths: IDs would cause unbounded cardinality and privacy risk.
    return path if isinstance(path, str) and len(path) <= 150 else 'unmatched'


async def access_middleware(request: Request, call_next):
    start = time.perf_counter()
    request_id = uuid.uuid4().hex
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers['X-Request-ID'] = request_id
        return response
    finally:
        elapsed = time.perf_counter() - start
        route = _route(request)
        if request.url.path != '/internal/metrics':
            _requests.labels(route, request.method, str(status)).inc()
            _latency.labels(route, request.method).observe(elapsed)
            _log.info(json.dumps({'event':'http','request_id':request_id,'method':request.method,'route':route,'status':status,'duration_ms':round(elapsed*1000,2)},ensure_ascii=False))


def serve_metrics(request: Request):
    token = os.getenv('SMARTBIZ_METRICS_TOKEN','')
    supplied = request.headers.get('authorization','')
    if not token or len(token)<24 or not secrets.compare_digest(supplied, 'Bearer '+token):
        raise HTTPException(404, 'Not found')
    return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)
