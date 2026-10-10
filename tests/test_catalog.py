"""Lesson 3: ensure the model's business lookup is read-only and public-only."""
from fastapi.testclient import TestClient
from app.main import app
from app.catalog import search_catalog


def test_catalog_function_returns_demo_data_only():
    result = search_catalog("知识库")
    assert result["demo_only"] is True
    assert result["source"] == "smartbiz-demo-catalog"
    assert result["count"] == 1
    assert result["items"][0]["id"] == "knowledge-base"
    assert "关键词" not in result["items"][0]
    assert "keywords" not in result["items"][0]
    assert "正式报价" in result["notice"]


def test_catalog_query_without_matches_is_empty():
    result = search_catalog("完全不相关的服务zzzz")
    assert result["count"] == 0
    assert result["items"] == []


def test_catalog_is_public_bounded_and_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTBIZ_DB_PATH", str(tmp_path / "unused.sqlite3"))
    with TestClient(app) as client:
        response = client.get("/api/catalog/search?q=%E5%B7%A5%E5%8D%95")
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == "ticket-workflow"
        assert client.get("/api/catalog/search?q=" + "a" * 81).status_code == 422
        assert client.post("/api/catalog/search", json={"q": "工单"}).status_code == 405
        assert client.get("/api/catalog/search").json()["count"] == 3
