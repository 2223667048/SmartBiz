"""Public, read-only DEMO service catalog. No private user or order data."""

# Replace this sample catalog with reviewed company data before external use.
_SERVICES = (
    {
        "id": "knowledge-base",
        "name": "企业知识库问答（演示服务）",
        "description": "基于企业批准的资料构建检索问答流程，可给出资料来源。",
        "pricing": "未提供正式报价；需要人工确认需求后评估",
        "keywords": ("知识库", "知识", "文档", "rag", "资料", "检索", "内部文档"),
    },
    {
        "id": "customer-service",
        "name": "AI 客服接待（演示服务）",
        "description": "处理常见问题、收集经用户同意的咨询线索，并支持人工跟进。",
        "pricing": "未提供正式报价；需要人工确认需求后评估",
        "keywords": ("客服", "机器人", "接待", "咨询", "对话", "聊天", "客户"),
    },
    {
        "id": "ticket-workflow",
        "name": "工单流程自动化（演示服务）",
        "description": "创建及跟进服务工单，后续可与内部 CRM 或流程系统对接。",
        "pricing": "未提供正式报价；需要人工确认需求后评估",
        "keywords": ("工单", "流程", "crm", "自动化", "审批", "跟进", "订单"),
    },
)

_GENERAL_TERMS = ("价格", "报价", "收费", "多少钱", "费用", "服务", "产品", "有哪些", "能做什么")


def search_catalog(query: str = "") -> dict:
    """Find public demo services; no dynamic SQL, no PII, no writes."""
    q = query.strip().casefold()
    if not q or q in _GENERAL_TERMS or q in ("有哪些服务", "有哪些产品", "你们提供哪些服务"):
        found = _SERVICES
    else:
        found = [
            item for item in _SERVICES
            if any(q in word.casefold() or word.casefold() in q for word in item["keywords"])
            or q in item["name"].casefold()
        ]

    # Keywords are internal search hints, not part of the API response.
    items = [{key: value for key, value in row.items() if key != "keywords"} for row in found]
    return {
        "source": "smartbiz-demo-catalog",
        "demo_only": True,
        "notice": "以下均为演示数据，不构成正式报价或产品承诺。",
        "count": len(items),
        "items": items,
    }
