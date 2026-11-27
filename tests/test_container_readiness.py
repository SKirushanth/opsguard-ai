import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import app


def test_readiness_initializes_storage_without_an_agent(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "workspace.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("AGENT_CHAT_URL", "http://127.0.0.1:1/chat")
    with TestClient(app) as client:
        assert client.get("/healthz").is_success
        assert not db_path.exists()
        assert client.get("/readyz").json() == {"status": "ready"}
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT count(*) FROM applications").fetchone() == (0,)
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path))  # a directory cannot be opened as a database
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").is_success
