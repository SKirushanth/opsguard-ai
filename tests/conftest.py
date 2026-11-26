import pytest


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("PROTECT_READS", "false")
    monkeypatch.setenv("AGENT_API_KEY", "")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
