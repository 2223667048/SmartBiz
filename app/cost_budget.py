"""Conservative per-tenant *estimated reservation*, NOT provider-billed usage.

Reserves a configured USD amount per Langflow request. Actual cloud billing may
exceed estimates; enforce model token limits and upstream provider quotas too.
"""
import math
import os
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException


def amounts():
    try:
        daily = Decimal(os.getenv('SMARTBIZ_DAILY_ESTIMATE_USD','2.00'))
        per_call = Decimal(os.getenv('SMARTBIZ_RESERVE_PER_CALL_USD','0.02'))
        if not daily.is_finite() or not per_call.is_finite() or daily <= 0 or per_call <= 0:
            raise ValueError('budget must be positive')
        cap = int((daily * 1_000_000).to_integral_value())
        unit = int((per_call * 1_000_000).to_integral_value())
        if not 1 <= unit <= cap <= 10**12:
            raise ValueError('budget size invalid')
        return cap, unit
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError('Invalid SMARTBIZ_DAILY_ESTIMATE_USD / SMARTBIZ_RESERVE_PER_CALL_USD') from exc


def reserve(actor, store):
    if os.getenv('SMARTBIZ_TENANT_BACKEND','sqlite') != 'postgres':
        # Explicitly do not claim atomic per-tenant billing controls on SQLite.
        return None
    cap, unit = amounts()
    try:
        ok = store.reserve_model_estimate(actor['organization_id'], cap, unit)
    except AttributeError as exc:
        raise RuntimeError('PostgreSQL model budget migration not applied') from exc
    if not ok:
        raise HTTPException(429, '企业今日模型调用预估预算已达上限', headers={'Retry-After':'3600'})
    return unit
