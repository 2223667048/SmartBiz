"""Lesson 12: conservative delivery gates, not an invoice/payment implementation.

These functions intentionally cannot declare Langflow-wide coverage based only on
metered proxy calls. The flow inventory is a human-audited input, not discovery.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Any

ALLOWED_ROUTING = {"gateway_verified", "legacy_direct", "not_tested", "disabled"}


def check_flow_inventory(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(records, list) or not records:
        raise ValueError("flow inventory must be a nonempty list")
    seen: set[str] = set()
    status: dict[str, int] = {k: 0 for k in sorted(ALLOWED_ROUTING)}
    missing: list[str] = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("invalid flow entry")
        ident = row.get("flow_id")
        routing = row.get("routing")
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ValueError("flow_id missing or repeated")
        seen.add(ident)
        if routing not in ALLOWED_ROUTING:
            raise ValueError("invalid routing status")
        if routing == "gateway_verified" and not row.get("verified_at"):
            raise ValueError("gateway_verified requires verified_at")
        status[routing] += 1
        if routing not in ("gateway_verified", "disabled"):
            missing.append(ident)
    return {
        "inventory_entries": len(records),
        "routing_counts": status,
        "unverified_active_flows": missing,
        "all_active_flows_operator_attested": not missing,
        "scope": "manual_inventory_only_not_runtime_proof",
    }


def gateway_reconciliation_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts CALLS, not tokens or unknown upstream traffic."""
    if not isinstance(calls, list):
        raise ValueError("calls must be list")
    stats = {"succeeded": 0, "uncertain": 0, "pending": 0, "other": 0,
             "reconciled": 0, "difference": 0, "missing_reconciliation": 0}
    for row in calls:
        state = row.get("status")
        stats[state if state in ("succeeded", "uncertain", "pending") else "other"] += 1
        if state == "succeeded":
            recon = row.get("reconciliation")
            if recon == "matched":
                stats["reconciled"] += 1
            elif recon == "difference":
                stats["difference"] += 1
            else:
                stats["missing_reconciliation"] += 1
    total = stats["succeeded"]
    return {
        **stats,
        "reconciled_success_ratio": (stats["reconciled"] / total if total else None),
        "measurement_scope": "gateway_call_rows_only",
        "provider_invoice_verified": False,
        "langflow_total_coverage_known": False,
    }


def release_gate(*, inventory: dict, gateway: dict, backup_restored: bool,
                 tenant_e2e_passed: bool, approved: bool) -> dict[str, Any]:
    blockers = []
    if not inventory.get("all_active_flows_operator_attested"):
        blockers.append("未确认所有活跃工作流的统一计量路径")
    if not gateway.get("succeeded", 0):
        blockers.append("没有可核对的成功模型调用样本")
    if gateway.get("uncertain", 0) or gateway.get("difference", 0):
        blockers.append("仍存在待核对或费用差异的模型请求")
    if gateway.get("missing_reconciliation", 0):
        blockers.append("成功调用尚未全部匹配经核实的供应商凭据")
    if not backup_restored:
        blockers.append("缺少可验证的独立备份恢复演练")
    if not tenant_e2e_passed:
        blockers.append("跨租户浏览器与数据库隔离测试未通过")
    if not approved:
        blockers.append("缺少人工发布批准")
    return {
        "decision": "ready_for_controlled_canary" if not blockers else "blocked",
        "blockers": blockers,
        "not_production_security_certification": True,
        "not_invoice_authorization": True,
    }


PAYMENT_STATES = {
    "draft": {"approved", "void"},
    "approved": {"invoice_issued", "void"},
    "invoice_issued": {"payment_pending", "void"},
    "payment_pending": {"paid", "failed"},
    "failed": {"payment_pending", "void"},
    "paid": {"refund_pending"},
    "refund_pending": {"refunded", "refund_failed"},
    "refund_failed": {"refund_pending"},
    "refunded": set(),
    "void": set(),
}


def validate_payment_transition(current: str, target: str) -> bool:
    """Pure design validator. Never moves funds or issues invoices."""
    if current not in PAYMENT_STATES or target not in PAYMENT_STATES[current]:
        raise ValueError("payment transition not allowed")
    return True
