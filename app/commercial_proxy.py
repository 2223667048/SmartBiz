"""Operator-provisioned, per-tenant OpenAI Chat Completions compatible proxy.

Never reuse a single proxy key for independent tenants. Streaming clients are
served from a fully buffered, metered upstream response so usage is never lost.
Each proxy request is metered via existing PG quota ledger, not counted twice.
"""
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from . import model_gateway as mg

log = logging.getLogger(__name__)
router=APIRouter(prefix='/v1', tags=['Commercial tenant model proxy'])
_ALIAS=re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


def configured_routes():
    base=mg.config()
    raw=os.environ.get('GATEWAY_ROUTES_JSON','').strip()
    if not raw:
        return {'tenant-default':base}
    if len(raw)>8192: raise mg.ConfigurationError('route config too large')
    try:
        items=json.loads(raw)
        if not isinstance(items,list) or not 1<=len(items)<=8: raise ValueError()
        routes={}
        for entry in items:
            alias=entry['alias']; model=entry['model']
            if not isinstance(alias,str) or not _ALIAS.fullmatch(alias) or not isinstance(model,str) or not _ALIAS.fullmatch(model):raise ValueError()
            if alias in routes:raise ValueError()
            prices={k:Decimal(str(entry[k])) for k in ('input','cached','output')}
            if any(not x.is_finite() or x<0 for x in prices.values()):raise ValueError()
            routes[alias]={**base,'model':model,'prices':prices}
        return routes
    except (ValueError,TypeError,KeyError,ArithmeticError) as e:
        raise mg.ConfigurationError('GATEWAY_ROUTES_JSON alias/model/prices format invalid') from e


def authenticate_service_key(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401,'缺少独立企业模型密钥')
    token=authorization[7:]
    if len(token)<32 or len(token)>256:
        raise HTTPException(401,'无效模型密钥')
    digest=hashlib.sha256(token.encode()).hexdigest()
    from .tenant_store_pg import _tx
    with _tx() as conn:
        row=conn.execute('SELECT organization_id,key_id FROM commercial_lookup_proxy_key(%s)',(digest,)).fetchone()
    if not row:
        raise HTTPException(401,'密钥已撤销或企业订阅停用')
    return {'id':'service:'+row['key_id'],'organization_id':row['organization_id'],'role':'member'}


class ProxyRequest(BaseModel):
    model: str = Field(min_length=1,max_length=64)
    messages: list[dict[str,Any]] = Field(min_length=1,max_length=40)
    max_completion_tokens: int = Field(default=350,ge=1,le=4096)
    max_tokens: int | None = Field(default=None,ge=1,le=4096)  # compatible legacy clients
    temperature: float | None = Field(default=None,ge=0,le=2)
    stream: bool = False
    tools: list[dict[str,Any]] | None = Field(default=None,max_length=16)
    tool_choice: str | dict[str,Any] | None = None


def make_payload(data: ProxyRequest, route: dict):
    msgs=data.messages
    for msg in msgs:
        if msg.get('role') not in ('system','developer','user','assistant','tool'):
            raise HTTPException(422,'role unsupported')
        if not isinstance(msg.get('content'),(str,type(None))):
            raise HTTPException(422,'仅允许文本及工具调用；不支持图像/音频内容')
        if msg.get('content') is None and msg['role']!='assistant':
            raise HTTPException(422,'content must be string')
        if 'tool_calls' in msg:
            if msg['role']!='assistant' or not isinstance(msg['tool_calls'],list):
                raise HTTPException(422,'invalid tool_calls')
        if len(json.dumps(msg,ensure_ascii=False))>20000:
            raise HTTPException(413,'单条消息过大')
    if data.tools and any(t.get('type')!='function' for t in data.tools):
        raise HTTPException(422,'仅支持 function tools')
    body={'model':route['model'],'messages':msgs,'max_completion_tokens':data.max_tokens or data.max_completion_tokens,'stream':False}
    if data.temperature is not None:body['temperature']=data.temperature
    if data.tools is not None:body['tools']=data.tools
    if data.tool_choice is not None:body['tool_choice']=data.tool_choice
    if len(json.dumps(body,ensure_ascii=False))>90000:raise HTTPException(413,'请求总大小超限')
    return body


def buffered_sse(upstream: dict):
    """Convert one complete, already-metered response into OpenAI SSE chunks."""
    common={
        'id':upstream.get('id',f'chatcmpl-{uuid.uuid4().hex}'),
        'object':'chat.completion.chunk',
        'created':upstream.get('created',int(datetime.now(timezone.utc).timestamp())),
        'model':upstream.get('model',''),
    }
    for choice in upstream.get('choices',[]):
        index=choice.get('index',0)
        message=choice.get('message') or {}
        first={**common,'choices':[{'index':index,'delta':{'role':message.get('role','assistant')},'finish_reason':None}]}
        yield f"data: {json.dumps(first,ensure_ascii=False,separators=(',',':'))}\n\n"
        if message.get('content') is not None:
            content={**common,'choices':[{'index':index,'delta':{'content':message['content']},'finish_reason':None}]}
            yield f"data: {json.dumps(content,ensure_ascii=False,separators=(',',':'))}\n\n"
        tool_calls=message.get('tool_calls')
        if tool_calls:
            calls={**common,'choices':[{'index':index,'delta':{'tool_calls':[
                {'index':i,**call} for i,call in enumerate(tool_calls)
            ]},'finish_reason':None}]}
            yield f"data: {json.dumps(calls,ensure_ascii=False,separators=(',',':'))}\n\n"
        final={**common,'choices':[{'index':index,'delta':{},'finish_reason':choice.get('finish_reason','stop')} ]}
        yield f"data: {json.dumps(final,ensure_ascii=False,separators=(',',':'))}\n\n"
    yield 'data: [DONE]\n\n'


@router.get('/models')
def list_models(actor=Depends(authenticate_service_key)):
    try: routes=configured_routes()
    except mg.ConfigurationError: raise HTTPException(503,'模型路由配置错误') from None
    return {'object':'list','data':[{'id':a,'object':'model','owned_by':'smartbiz'} for a in routes]}


@router.post('/chat/completions')
async def completions(data:ProxyRequest, response:Response,
                      actor=Depends(authenticate_service_key),
                      idempotency_key: str | None = Header(default=None,alias='Idempotency-Key')):
    from . import gateway_ledger_pg as ledger
    if os.getenv('GATEWAY_ENABLED','false').lower()!='true':
        raise HTTPException(503,'网关未启用')
    if os.getenv('GATEWAY_ENABLED','false').lower()!='true':
        raise HTTPException(503,'网关未启用')
    try:routes=configured_routes()
    except mg.ConfigurationError:raise HTTPException(503,'模型路由配置错误') from None
    conf=routes.get(data.model)
    if not conf:raise HTTPException(422,'未经授权的模型路由')
    payload=make_payload(data,conf)
    if idempotency_key:
        try: request_id=str(uuid.UUID(idempotency_key))
        except ValueError:raise HTTPException(422,'Idempotency-Key 必须为 UUID') from None
    else:
        request_id=str(uuid.uuid4())  # Client library may not supply an idempotency key.
    try:
        call_id=ledger.reserve(actor,request_id,conf['model'],datetime.now(timezone.utc).date(),
                               conf['daily_micro'],conf['hold_micro'],conf['prices'])
    except ledger.BudgetExceeded:
        raise HTTPException(429,'企业每日额度不足',headers={'Retry-After':'3600'}) from None
    except ledger.DuplicateRequest:
        raise HTTPException(409,'幂等请求已被处理') from None
    except (ledger.SubscriptionInactive,ledger.PlanQuotaExceeded):
        raise HTTPException(402,'订阅无效或月度额度不足') from None
    try:
        upstream,usage=await mg.provider_raw_chat(payload,conf)
        micro=mg.calculate_cost_micro(usage,conf['prices'])
    except (mg.UpstreamError,mg.UnknownUsage):
        ledger.uncertain(actor,call_id)
        raise HTTPException(502,'供应商调用失败或 usage 缺失；预算预留待核对') from None
    ledger.succeed(actor,call_id,str(upstream.get('id',''))[:160],usage,micro)
    if data.stream:
        return StreamingResponse(buffered_sse(upstream),media_type='text/event-stream',headers={
            'X-SmartBiz-Call-ID':call_id,
            'Cache-Control':'no-cache',
            'X-Accel-Buffering':'no',
        })
    response.headers['X-SmartBiz-Call-ID']=call_id
    # Original tool_calls/finish_reason retained for Agent clients. Usage returned by provider.
    return upstream
