"""Test tool logic without pretending to have a running Langflow installation."""
import importlib.util
import json
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _load_with_fake_lfx(monkeypatch):
    pkg = types.ModuleType("lfx")
    pkg.__path__ = []
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
    io.MessageTextInput = Input
    io.Output = Input
    schema.Data = Data
    for name, module in (("lfx", pkg), ("lfx.custom", custom), ("lfx.io", io), ("lfx.schema", schema)):
        monkeypatch.setitem(sys.modules, name, module)

    toolpath = Path(__file__).resolve().parent.parent / "extensions" / "smartbiz_catalog_tool.py"
    spec = importlib.util.spec_from_file_location("smartbiz_integration_test_tool", toolpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_custom_tool_calls_fixed_internal_endpoint(monkeypatch):
    module = _load_with_fake_lfx(monkeypatch)
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, length):
            assert length <= 32768
            return json.dumps({"reference_only": True, "count": 1, "items": []}).encode()

    def fake_urlopen(req, timeout):
        assert timeout == 5
        seen.append(req.full_url)
        return Response()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    tool = module.SmartBizCatalogTool()
    tool.query = "知识库"
    result = tool.search_services()
    assert result.data["reference_only"] is True
    assert urlsplit(seen[0]).netloc == "smartbiz:8000"
    assert urlsplit(seen[0]).path == "/api/catalog/search"
    assert parse_qs(urlsplit(seen[0]).query) == {"q": ["知识库"]}


def test_custom_tool_rejects_too_long_query_without_network(monkeypatch):
    module = _load_with_fake_lfx(monkeypatch)

    def should_not_call(*args, **kwargs):
        raise AssertionError("Must reject before using network")

    monkeypatch.setattr(module, "urlopen", should_not_call)
    tool = module.SmartBizCatalogTool()
    tool.query = "Z" * 81
    result = tool.search_services()
    assert "error" in result.data
