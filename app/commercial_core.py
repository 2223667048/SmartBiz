"""Pure, auditable quote/reconciliation arithmetic. No remote calls or funds movement."""
from decimal import Decimal, ROUND_HALF_UP


def draft_invoice(fee_micro: int, included_micro: int, gateway_micro: int,
                  multiplier='1.20') -> dict:
    """Provisional illustration: subscription fee + estimated gateway overage.

    Not an invoice: actual provider adjustments, taxes, payments and Langflow
    calls not routed through the gateway are not represented.
    """
    if min(fee_micro, included_micro, gateway_micro) < 0:
        raise ValueError('negative amount')
    rate = Decimal(multiplier)
    if not rate.is_finite() or rate < 1 or rate > 10:
        raise ValueError('invalid multiplier')
    excess = max(0, gateway_micro - included_micro)
    overage = int((Decimal(excess) * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return {'currency':'USD', 'amount_unit':'micro_usd',
            'subscription_fee_micro_usd':fee_micro,
            'included_gateway_cost_micro_usd':included_micro,
            'gateway_internal_cost_micro_usd':gateway_micro,
            'provisional_overage_micro_usd':overage,
            'illustrative_total_micro_usd':fee_micro+overage,
            'payable_micro_usd':None, 'status':'draft_unverified',
            'coverage':'metered_gateway_only', 'not_an_invoice':True}


def classify_reconciliation(matches: list, provider_cost_micro: int, tolerance_micro: int=1):
    """Only one exact provider request id is matchable. Never guess a tenant."""
    if len(matches) == 0:
        return {'status':'unmatched','call_id':None,'org_id':None,'gateway_cost':None,'delta':None}
    if len(matches) != 1 or matches[0]['calculated_micro_usd'] is None:
        return {'status':'ambiguous','call_id':None,'org_id':None,'gateway_cost':None,'delta':None}
    row=matches[0]
    delta=provider_cost_micro-row['calculated_micro_usd']
    return {'status':'matched' if abs(delta)<=tolerance_micro else 'difference',
            'call_id':row['id'],'org_id':row['organization_id'],
            'gateway_cost':row['calculated_micro_usd'],'delta':delta}
