"""Verify the real-mode HTTP request contract WITHOUT a paid model call."""
import asyncio
import httpx
import pytest
from app import langflow


def test_real_mode_sends_valid_session_id_and_server_side_key(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "langflow")
    monkeypatch.setenv("LANGFLOW_URL", "http://langflow:7860")
    monkeypatch.setenv("LANGFLOW_FLOW_ID", "flow-example")
    monkeypatch.setenv("LANGFLOW_API_KEY", "internal-test-secret")
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json, headers):
            seen.update({"url": url, "json": json, "headers": headers})
            return httpx.Response(
                200,
                json={"outputs": [{"outputs": [{"results": {"message": {"text": "真实模式的模拟网络响应"}}}]}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(langflow.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(langflow.ask_agent("我的代号是蓝鲸7412", "session-abc"))
    assert result == "真实模式的模拟网络响应"
    assert seen["json"]["session_id"] == "session-abc"
    assert seen["json"]["input_value"] == "我的代号是蓝鲸7412"
    assert seen["headers"]["x-api-key"] == "internal-test-secret"
    assert seen["url"] == "http://langflow:7860/api/v1/run/flow-example"


def test_real_mode_missing_configuration_fails_before_network(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "langflow")
    monkeypatch.setenv("LANGFLOW_FLOW_ID", "")
    monkeypatch.setenv("LANGFLOW_API_KEY", "")
    with pytest.raises(langflow.AgentError):
        asyncio.run(langflow.ask_agent("测试", "session-abc"))
