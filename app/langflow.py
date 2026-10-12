"""Adapter: your business code only knows ask_agent(), not Langflow internals."""
import os
import httpx
from .settings import agent_mode, langflow_url


class AgentError(Exception):
    """Network error, invalid configuration, or unexpected upstream result."""


def extract_reply(payload: dict) -> str:
    """Based on Langflow v1 /run response. Fail closed if the schema differs."""
    try:
        outputs = payload["outputs"]
        for stage in reversed(outputs):
            for item in reversed(stage["outputs"]):
                results = item.get("results", {})
                message = results.get("message", {})
                if isinstance(message, dict):
                    text = message.get("text") or message.get("data", {}).get("text")
                else:
                    text = None
                if isinstance(text, str) and text.strip():
                    return text.strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise AgentError("Langflow output structure changed") from exc
    raise AgentError("Langflow did not return a chat message")


async def ask_agent(text: str, session_id: str) -> str:
    mode = agent_mode()
    if mode == "mock":
        if any(word in text for word in ("价格", "报价", "费用")):
            return "本地回退模式：具体报价需要了解业务需求。你可以点击下方‘留下咨询需求’提交联系方式。"
        return f"本地回退模式：我收到了你的问题：{text}。配置 Langflow 工作流后，这里将返回模型回答。"
    if mode != "langflow":
        raise AgentError("Unsupported AGENT_MODE")

    flow_id = os.getenv("LANGFLOW_FLOW_ID", "").strip()
    api_key = os.getenv("LANGFLOW_API_KEY", "").strip()
    if not flow_id or not api_key:
        raise AgentError("LANGFLOW_FLOW_ID and LANGFLOW_API_KEY are required")

    # The upstream URL is server-side configuration, never supplied by visitors.
    url = f"{langflow_url()}/api/v1/run/{flow_id}"
    body = {"input_value": text, "input_type": "chat", "output_type": "chat",
            "session_id": session_id, "output_component": ""}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(65, connect=10)) as client:
            result = await client.post(url, json=body, headers={
                "Content-Type": "application/json", "x-api-key": api_key})
            result.raise_for_status()
            return extract_reply(result.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise AgentError("Langflow request failed") from exc
