"""Only validates tool Python interface in isolation; not real Langflow runtime."""
import importlib.util
import json
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def load_component(monkeypatch):
    pkg = types.ModuleType("lfx"); pkg.__path__ = []
    custom = types.ModuleType("lfx.custom")
    io = types.ModuleType("lfx.io")
    schema = types.ModuleType("lfx.schema")

    class Component:
        pass

    class Input:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Data:
        def __init__(self, data):
            self.data = data

    custom.Component = Component
    io.MessageTextInput = io.Output = Input
    schema.Data = Data
    for name, module in (("lfx", pkg), ("lfx.custom", custom), ("lfx.io", io), ("lfx.schema", schema)):
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parent.parent / "extensions" / "smartbiz_knowledge_tool.py"
    spec = importlib.util.spec_from_file_location("kb_tool_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_knowledge_tool_uses_fixed_url_and_service_header(monkeypatch):
    monkeypatch.setenv("KB_SERVICE_KEY", "internal-service-token")
    module = load_component(monkeypatch)
    requests = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, maximum):
            assert maximum == 50001
            return json.dumps({"count": 1, "sources": [{"citation": "[1]"}]}).encode()

    def fake_urlopen(req, timeout):
        requests.append(req)
        assert timeout == 12
        return Response()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    component = module.SmartBizKnowledgeTool()
    component.query = "客服在线时间"
    result = component.search_knowledge()
    assert result.data["count"] == 1
    parsed = urlsplit(requests[0].full_url)
    assert parsed.netloc == "smartbiz:8000"
    assert parsed.path == "/api/kb/search"
    assert parse_qs(parsed.query) == {"q": ["客服在线时间"], "limit": ["3"]}
    assert requests[0].get_header("X-kb-service-key") == "internal-service-token"


def test_knowledge_tool_fails_closed_without_key_or_invalid_query(monkeypatch):
    monkeypatch.delenv("KB_SERVICE_KEY", raising=False)
    module = load_component(monkeypatch)
    def unreachable(*args, **kwargs):
        raise AssertionError("should not connect")
    monkeypatch.setattr(module, "urlopen", unreachable)
    component = module.SmartBizKnowledgeTool()
    component.query = "查询"
    assert "error" in component.search_knowledge().data
    monkeypatch.setenv("KB_SERVICE_KEY", "unit-test")
    component.query = "a" * 1001
    assert "error" in component.search_knowledge().data
