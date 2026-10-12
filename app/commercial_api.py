"""Read-only tenant subscription and draft billing. Operator mutations use CLI."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from .tenant_api import require_manager, require_actor
from . import commercial_proxy

router=APIRouter(prefix='/api/v2/commercial', tags=['Lesson 11 SaaS reports'])

@router.get('/subscription')
def subscription(actor=Depends(require_actor)):
    from .model_gateway_api import configuration
    configuration()
    from . import commercial_pg
    row=commercial_pg.entitlement(actor)
    if not row:raise HTTPException(404,'企业没有订阅')
    return row

@router.get('/routes')
def routes(actor=Depends(require_actor)):
    from .model_gateway_api import configuration
    configuration()
    try: data=commercial_proxy.configured_routes()
    except Exception:raise HTTPException(503,'模型路由配置错误') from None
    return {'models':[{'alias':a,'model':x['model']} for a,x in data.items()],
            'scope':'metered_gateway_only'}

@router.get('/billing/draft')
def billing_draft(year:int=Query(ge=2020,le=2100),month:int=Query(ge=1,le=12),
                  actor=Depends(require_manager)):
    from .model_gateway_api import configuration
    configuration()
    from . import commercial_pg
    return commercial_pg.monthly_draft(actor,year,month)

@router.get('/reconciliation')
def reconciliation(actor=Depends(require_manager)):
    from .model_gateway_api import configuration
    configuration()
    from . import commercial_pg
    return commercial_pg.reconciliation_overview(actor)
