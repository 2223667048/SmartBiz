from pathlib import Path
import pytest
from app.delivery_control import (
    check_flow_inventory, gateway_reconciliation_summary,
    release_gate, validate_payment_transition)


def test_flow_coverage_refuses_unverified_flows():
    x=check_flow_inventory([{'flow_id':'a','routing':'gateway_verified','verified_at':'2026-10-12'},
                            {'flow_id':'b','routing':'legacy_direct'}])
    assert x['all_active_flows_operator_attested'] is False
    assert x['unverified_active_flows']==['b']
    with pytest.raises(ValueError):
        check_flow_inventory([{'flow_id':'a','routing':'gateway_verified'}])
    with pytest.raises(ValueError):
        check_flow_inventory([{'flow_id':'a','routing':'disabled'}, {'flow_id':'a','routing':'disabled'}])


def test_reconciliation_does_not_claim_untracked_flows():
    x=gateway_reconciliation_summary([{'status':'succeeded','reconciliation':'matched'},
                                     {'status':'succeeded','reconciliation':'difference'},
                                     {'status':'uncertain','reconciliation':None}])
    assert x['reconciled_success_ratio']==.5
    assert x['langflow_total_coverage_known'] is False
    assert x['uncertain']==1
    assert gateway_reconciliation_summary([])['reconciled_success_ratio'] is None


def test_gate_fails_closed_until_all_checks_are_true():
    inv=check_flow_inventory([{'flow_id':'a','routing':'gateway_verified','verified_at':'2026-10-12'}])
    stats=gateway_reconciliation_summary([{'status':'succeeded','reconciliation':'matched'}])
    fail=release_gate(inventory=inv,gateway=stats,backup_restored=False,
                      tenant_e2e_passed=True,approved=True)
    assert fail['decision']=='blocked'
    ok=release_gate(inventory=inv,gateway=stats,backup_restored=True,
                    tenant_e2e_passed=True,approved=True)
    assert ok['decision']=='ready_for_controlled_canary'
    zero=release_gate(inventory=inv,gateway=gateway_reconciliation_summary([]),
                      backup_restored=True,tenant_e2e_passed=True,approved=True)
    assert zero['decision']=='blocked'
    assert ok['not_production_security_certification']


def test_payment_design_cannot_skip_state_or_charge():
    assert validate_payment_transition('draft','approved')
    with pytest.raises(ValueError):
        validate_payment_transition('draft','paid')
    with pytest.raises(ValueError):
        validate_payment_transition('refunded','paid')


def test_langflow_override_explicitly_mounts_database_and_keeps_ssrf_on():
    p=Path(__file__).resolve().parents[1]/'docker-compose.langflow-safe.yml'
    txt=p.read_text(encoding='utf-8')
    assert 'LANGFLOW_DATABASE_URL' in txt
    assert 'sqlite:////app/langflow/langflow.db' in txt
    assert 'LANGFLOW_SSRF_PROTECTION_ENABLED' in txt
    assert 'LANGFLOW_SSRF_ALLOWED_HOSTS' in txt
    assert '\n    volumes:' not in txt  # inherits existing named volume; never replaces it
