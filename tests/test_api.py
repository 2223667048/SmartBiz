import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTBIZ_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-for-tests-only")
    with TestClient(app) as c:
        yield c


def session_headers(client):
    r = client.post("/api/sessions")
    assert r.status_code == 201
    return {"X-Session-Token": r.json()["session_token"]}


def test_home_and_health(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_chat_and_feedback(client):
    headers = session_headers(client)
    assert client.post("/api/chat", json={"message": "Hi"}).status_code == 401
    r = client.post("/api/chat", headers=headers, json={"message": "怎么收费？"})
    assert r.status_code == 200
    assert "演示模式" in r.json()["reply"]
    mid = r.json()["message_id"]
    assert client.post("/api/feedback", headers=headers,
                       json={"message_id": mid, "score": 1}).status_code == 200
    other = session_headers(client)
    assert client.post("/api/feedback", headers=other,
                       json={"message_id": mid, "score": 1}).status_code == 404
    assert client.post("/api/chat", headers=headers, json={"message": " "}).status_code == 422


def test_leads_consent_and_admin(client):
    headers = session_headers(client)
    lead = {"name": "访客", "contact": "person@example.com", "requirement": "对接内部知识库", "consent": False}
    assert client.post("/api/leads", headers=headers, json=lead).status_code == 422
    lead["consent"] = True
    assert client.post("/api/leads", headers=headers, json=lead).status_code == 201
    assert client.get("/api/admin/leads").status_code == 403
    admin = {"X-Admin-Key": "admin-for-tests-only"}
    data = client.get("/api/admin/stats", headers=admin).json()
    assert data["leads"] == 1
    assert client.get("/api/admin/leads", headers=admin).json()[0]["name"] == "访客"


def test_agent_upstream_error_is_safe(client, monkeypatch):
    async def fail(text, sid):
        from app.langflow import AgentError
        raise AgentError("private internal details")
    monkeypatch.setattr("app.main.ask_agent", fail)
    r = client.post("/api/chat", headers=session_headers(client), json={"message": "hi"})
    assert r.status_code == 502
    assert "private internal details" not in r.text


def test_tickets_isolation_and_status(client):
    alice = session_headers(client)
    bob = session_headers(client)
    payload = {"subject": "需要人工回复", "description": "请帮助处理业务集成"}
    result = client.post("/api/tickets", headers=alice, json=payload)
    assert result.status_code == 201
    ticket_id = result.json()["ticket_id"]
    assert client.get("/api/tickets", headers=bob).json() == []
    assert client.get("/api/tickets", headers=alice).json()[0]["status"] == "new"
    assert client.patch(f"/api/admin/tickets/{ticket_id}", json={"status": "resolved"}).status_code == 403
    admin = {"X-Admin-Key": "admin-for-tests-only"}
    assert client.patch(f"/api/admin/tickets/{ticket_id}", headers=admin,
                        json={"status": "unknown"}).status_code == 422
    assert client.patch(f"/api/admin/tickets/{ticket_id}", headers=admin,
                        json={"status": "resolved"}).status_code == 200
    assert client.get("/api/admin/tickets", headers=admin).json()[0]["status"] == "resolved"


def test_message_history_is_private_ordered_and_limited(client):
    alice = session_headers(client)
    bob = session_headers(client)
    assert client.get("/api/messages").status_code == 401
    assert client.get("/api/messages", headers=alice).json() == []

    result = client.post("/api/chat", headers=alice, json={"message": "Alice的问题"})
    assert result.status_code == 200
    client.post("/api/chat", headers=bob, json={"message": "Bob的问题"})

    rows = client.get("/api/messages", headers=alice).json()
    assert len(rows) == 2
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "Alice的问题"
    assert rows[1]["id"] == result.json()["message_id"]
    assert all("Bob的问题" not in row["content"] for row in rows)
    assert all("session_id" not in row for row in rows)

    only_latest = client.get("/api/messages?limit=1", headers=alice)
    assert only_latest.status_code == 200
    assert only_latest.json()[0]["role"] == "assistant"
    assert client.get("/api/messages?limit=0", headers=alice).status_code == 422
    assert client.get("/api/messages?limit=101", headers=alice).status_code == 422
    assert len(client.get("/api/messages", headers=bob).json()) == 2
