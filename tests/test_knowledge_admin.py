"""Admin UI privilege, CSRF, search/answer, cloud reindex and rollback tests."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import embeddings, kb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTBIZ_DB_PATH", str(tmp_path / "knowledge-admin.sqlite3"))
    monkeypatch.setenv("ADMIN_API_KEY", "strong-test-admin-password-never-use")
    monkeypatch.setenv("KB_SERVICE_KEY", "strong-test-service-password-never-use")
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "demo")
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("RAG_MIN_SIMILARITY", "0.25")
    with TestClient(app) as c:
        yield c


def login(client):
    r = client.post("/api/admin/ui/login", json={"password": "strong-test-admin-password-never-use"})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def upload(client, headers):
    return client.post("/api/kb/documents", headers=headers,
                       files={"file": ("policy.md", "企业售后政策：工作日客服9:00至18:00。" * 18, "text/markdown")})


def test_login_cookie_auth_csrfs_and_logout(client):
    assert client.get("/admin").status_code == 200
    assert client.get("/api/admin/ui/me").status_code == 401
    assert client.get("/api/kb/documents").status_code == 403
    assert client.get("/api/admin/kb/index").status_code == 403
    assert client.post("/api/admin/ui/login", json={"password": "wrong"}).status_code == 403
    csrf = login(client)
    setcookie = client.cookies.get("smartbiz_admin_session")
    assert setcookie
    assert client.get("/api/admin/ui/me").json()["csrf_token"] == csrf["X-CSRF-Token"]
    assert client.get("/api/admin/kb/index").json()["document_count"] == 0
    assert client.post("/api/kb/documents", files={"file": ("a.txt", "hello world")}).status_code == 403
    assert client.post("/api/kb/documents", headers={"X-CSRF-Token": "bad"},
                       files={"file": ("a.txt", "hello world")}).status_code == 403
    assert client.post("/api/kb/documents", headers={**csrf, "Origin": "https://malicious.example"},
                       files={"file": ("a.txt", "hello world")}).status_code == 403
    assert upload(client, csrf).status_code == 201
    assert client.get("/api/kb/documents").status_code == 200
    assert client.post("/api/admin/ui/logout").status_code == 403
    assert client.post("/api/admin/ui/logout", headers=csrf).json() == {"logged_out": True}
    assert client.get("/api/kb/documents").status_code == 403


def test_index_rebuild_demo_to_cloud_mock_without_reupload(client, monkeypatch):
    csrf = login(client)
    res = upload(client, csrf)
    assert res.status_code == 201
    docid = res.json()["document_id"]
    initial = client.get("/api/admin/kb/index").json()
    assert initial["compatible"] and initial["chunk_count"] >= 1
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "openai")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("RAG_EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    broken = client.get("/api/admin/kb/index").json()
    assert broken["requires_reindex"]
    assert client.get("/api/admin/ui/kb/search?q=客服").status_code == 409
    assert client.post("/api/admin/kb/reindex", headers=csrf, json={"confirm": "NO"}).status_code == 422
    assert client.post("/api/admin/kb/reindex", json={"confirm": "REINDEX"}).status_code == 403
    async def fake_cloud(texts):
        # deterministic 3-dimensional stand-in for a real provider call
        return [embeddings.normalize([float(len(x)), float(x.count("客服") + 1), 2.0]) for x in texts]
    monkeypatch.setattr("app.main.embeddings.embed_many", fake_cloud)
    response = client.post("/api/admin/kb/reindex", headers=csrf, json={"confirm": "REINDEX"})
    assert response.status_code == 200, response.text
    assert response.json()["chunks_reindexed"] == broken["chunk_count"]
    final = client.get("/api/admin/kb/index").json()
    assert final["compatible"] and not final["requires_reindex"]
    docs = client.get("/api/kb/documents").json()
    assert docs[0]["id"] == docid  # reindex did not delete and reupload documents
    assert docs[0]["embedding_spec"] == embeddings.index_spec()
    result = client.get("/api/admin/ui/kb/search?q=客服&limit=3")
    assert result.status_code == 200
    assert result.json()["sources"][0]["document_id"] == docid


def test_failed_cloud_reindex_keeps_original_vectors(client, monkeypatch):
    csrf = login(client)
    assert upload(client, csrf).status_code == 201
    before = kb.index_status(embeddings.index_spec())
    before_chunks = kb.read_chunks_for_reindex()
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "openai")
    async def fail_cloud(_texts):
        raise embeddings.EmbeddingError("provider unreachable")
    monkeypatch.setattr("app.main.embeddings.embed_many", fail_cloud)
    failure = client.post("/api/admin/kb/reindex", headers=csrf, json={"confirm": "REINDEX"})
    assert failure.status_code == 502
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "demo")
    async def demo_embed(texts):
        return [embeddings.demo_hash_embedding(t) for t in texts]
    monkeypatch.setattr("app.main.embeddings.embed_many", demo_embed)
    after = kb.index_status(embeddings.index_spec())
    assert after == before
    assert kb.read_chunks_for_reindex() == before_chunks
    assert client.get("/api/admin/ui/kb/search?q=售后").status_code == 200


def test_rag_answer_and_sources_through_admin_browser(client, monkeypatch):
    csrf = login(client)
    assert upload(client, csrf).status_code == 201
    assert client.post("/api/admin/ui/kb/ask", json={"question": "客服几点"}).status_code == 403
    assert client.post("/api/admin/ui/kb/ask", headers=csrf,
                       json={"question": "工作日客服9:00至18:00"}).status_code == 503
    monkeypatch.setenv("AGENT_MODE", "langflow")
    captured = {}
    async def fake_agent(prompt, session_id):
        captured["session"] = session_id
        return "根据售后政策，工作日客服时间为9:00至18:00。[1]"
    monkeypatch.setattr("app.main.ask_agent", fake_agent)
    response = client.post("/api/admin/ui/kb/ask", headers=csrf,
                           json={"question": "工作日客服时间", "limit": 3})
    assert response.status_code == 200, response.text
    assert response.json()["sources"]
    assert response.json()["evidence_status"] == "retrieved_not_verified"
    assert captured["session"].startswith("admin-kb:")


def test_index_changed_during_remote_embedding_is_not_overwritten(client, monkeypatch):
    csrf = login(client)
    assert upload(client, csrf).status_code == 201
    snapshot = kb.read_chunks_for_reindex()
    vector = embeddings.demo_hash_embedding("hello")
    # Simulate concurrent removal after expensive embedding network requests.
    docid = client.get("/api/kb/documents").json()[0]["id"]
    assert client.delete(f"/api/kb/documents/{docid}", headers=csrf).status_code == 204
    with pytest.raises(kb.KbConflictError):
        kb.replace_all_vectors(snapshot, [vector] * len(snapshot), embeddings.index_spec())
    assert client.get("/api/kb/documents").json() == []


def test_probe_embedding_does_not_change_index(client, monkeypatch):
    csrf = login(client)
    assert upload(client, csrf).status_code == 201
    spec_before = client.get('/api/admin/kb/index').json()
    probe = client.post('/api/admin/kb/embedding-test', headers=csrf,
                        json={'text': '测试知识库语义模型'})
    assert probe.status_code == 200
    assert probe.json()['dimension'] == 256
    assert probe.json()['index_modified'] is False
    assert client.get('/api/admin/kb/index').json() == spec_before
    assert client.post('/api/admin/kb/embedding-test', json={'text': 'testing'}).status_code == 403


def test_embedding_dimension_configuration_changes_index_fingerprint(monkeypatch):
    monkeypatch.setenv('RAG_EMBEDDING_MODE', 'openai')
    monkeypatch.setenv('RAG_EMBEDDING_MODEL', 'text-embedding-3-small')
    monkeypatch.setenv('RAG_EMBEDDING_BASE_URL', 'https://api.openai.com/v1')
    monkeypatch.delenv('RAG_EMBEDDING_DIMENSIONS', raising=False)
    default = embeddings.index_spec()
    monkeypatch.setenv('RAG_EMBEDDING_DIMENSIONS', '512')
    assert default != embeddings.index_spec()
    monkeypatch.setenv('RAG_EMBEDDING_DIMENSIONS', 'bad')
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.index_spec()


def test_cloud_probe_payload_with_dimensions_and_no_leaked_key(client, monkeypatch):
    import httpx
    import json
    monkeypatch.setenv('RAG_EMBEDDING_MODE', 'openai')
    monkeypatch.setenv('RAG_EMBEDDING_MODEL', 'text-embedding-3-small')
    monkeypatch.setenv('RAG_EMBEDDING_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('RAG_EMBEDDING_API_KEY', 'test-cloud-key-do-not-log')
    monkeypatch.setenv('RAG_EMBEDDING_DIMENSIONS', '3')
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        assert req.headers['authorization'] == 'Bearer test-cloud-key-do-not-log'
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [3.0, 0.0, 4.0]}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(embeddings.httpx, 'AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    csrf = login(client)
    probe = client.post('/api/admin/kb/embedding-test', headers=csrf, json={'text': 'test input'})
    assert probe.status_code == 200, probe.text
    assert probe.json()['dimension'] == 3
    assert probe.json()['cloud_mode'] is True
    assert 'test-cloud-key' not in str(probe.json())
    assert seen == [{'model': 'text-embedding-3-small', 'input': ['test input'], 'dimensions': 3}]
    assert client.get('/api/admin/kb/index').json()['document_count'] == 0


def test_changing_admin_secret_revokes_old_browser_sessions(client, monkeypatch):
    login(client)
    assert client.get('/api/admin/ui/me').status_code == 200
    monkeypatch.setenv('ADMIN_API_KEY', 'a-different-rotated-admin-password')
    assert client.get('/api/admin/ui/me').status_code == 401
    assert client.get('/api/kb/documents').status_code == 403
