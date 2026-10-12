"""Metered single-step non-streaming OpenAI-compatible chat gateway.

Does NOT meter Langflow agent-internal calls, embeddings, external tools, or
failed requests with unknown provider billing. No user-provided upstream URL.
"""
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from urllib.parse import urlsplit
import httpx


class ConfigurationError(RuntimeError):
    pass


class UpstreamError(RuntimeError):
    pass


class UnknownUsage(RuntimeError):
    pass


def config():
    base = os.getenv('GATEWAY_PROVIDER_BASE_URL','https://api.openai.com/v1').rstrip('/')
    parts = urlsplit(base)
    allowed = [h.strip().lower() for h in os.getenv('GATEWAY_ALLOWED_HOSTS','api.openai.com').split(',') if h.strip()]
    if parts.scheme != 'https' or parts.hostname not in allowed or parts.username or parts.password or parts.query or parts.fragment or parts.port not in (None,443):
        raise ConfigurationError('GATEWAY_PROVIDER_BASE_URL 必须为明确授权的 HTTPS 服务地址')
    key = os.getenv('GATEWAY_PROVIDER_API_KEY','').strip()
    model = os.getenv('GATEWAY_MODEL','').strip()
    if not key or not model or len(model)>128 or not all(ch.isalnum() or ch in '._-' for ch in model):
        raise ConfigurationError('缺少有效的 GATEWAY_MODEL / GATEWAY_PROVIDER_API_KEY')
    try:
        prices = {k:Decimal(os.environ[f'GATEWAY_PRICE_{k.upper()}_USD_PER_1M']) for k in ('input','output','cached')}
        daily = Decimal(os.getenv('GATEWAY_DAILY_LIMIT_USD','2.00'))
        hold = Decimal(os.getenv('GATEWAY_CALL_HOLD_USD','0.10'))
        if any(not v.is_finite() or v < 0 for v in prices.values()) or not daily.is_finite() or not hold.is_finite() or daily<=0 or hold<=0 or hold>daily:
            raise ValueError('invalid prices')
    except (KeyError,ValueError,InvalidOperation) as exc:
        raise ConfigurationError('必须显式配置非负模型单价、正数配额和调用预留金额') from exc
    return {'base':base,'api_key':key,'model':model,'prices':prices,
            'daily_micro':to_micro(daily),'hold_micro':to_micro(hold)}


def to_micro(usd):
    return int((Decimal(usd)*Decimal('1000000')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def parse_usage(result):
    raw = result.get('usage')
    if not isinstance(raw,dict):
        raise UnknownUsage('供应商未返回 usage；不能声明为零消耗')
    p=raw.get('prompt_tokens'); c=raw.get('completion_tokens')
    details=raw.get('prompt_tokens_details') or {}
    if not isinstance(details,dict): raise UnknownUsage('usage 格式错误')
    cached=details.get('cached_tokens',0)
    if any(type(n) is not int or n<0 for n in (p,c,cached)) or cached>p:
        raise UnknownUsage('供应商 usage 缺少或无效')
    return {'prompt_tokens':p,'completion_tokens':c,'cached_prompt_tokens':cached}


def calculate_cost_micro(usage, prices):
    # Prices are USD / 1,000,000 tokens. Multiply tokens * price = micro-USD.
    uncached=usage['prompt_tokens']-usage['cached_prompt_tokens']
    return int((uncached*prices['input'] + usage['cached_prompt_tokens']*prices['cached'] +
                usage['completion_tokens']*prices['output']).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


async def provider_completion(question: str, max_output_tokens: int, conf: dict):
    body={'model':conf['model'],
          'messages':[{'role':'system','content':'你是企业助手。仅回答给定问题；不要泄露凭证。'},
                      {'role':'user','content':question}],
          'max_completion_tokens':max_output_tokens,'stream':False}
    # Never forward tenant credentials or arbitrary client-supplied headers to provider.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(75.0,connect=10.0),follow_redirects=False) as client:
            resp=await client.post(conf['base']+'/chat/completions',json=body,
                                   headers={'Authorization':'Bearer '+conf['api_key']})
            if resp.is_redirect or resp.status_code>=400:
                raise UpstreamError('上游模型服务没有完成请求')
            data=resp.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise UpstreamError('模型服务通信异常，计费结果可能未知') from exc
    try:
        answer=data['choices'][0]['message']['content']
        if not isinstance(answer,str) or not answer: raise ValueError('empty completion')
        usage=parse_usage(data)
        return answer,usage,str(data.get('id',''))[:160]
    except (KeyError,IndexError,TypeError,ValueError,UnknownUsage) as exc:
        raise UnknownUsage('供应商输出或 usage 格式不完整，计费状态未知') from exc
