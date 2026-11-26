from pathlib import Path
from shutil import copyfile

import pytest
from fastapi.testclient import TestClient

from backend.app import services
from backend.app.main import app

client = TestClient(app)


def test_service_catalog() -> None:
    response = client.get("/api/services")
    assert response.status_code == 200
    assert response.json() == {
        "services": [
            {"id": "auth", "name": "Authentication Service", "status": "healthy"},
            {"id": "orders", "name": "Order Service", "status": "down"},
            {"id": "notifications", "name": "Notification Service", "status": "healthy"},
        ]
    }


@pytest.mark.parametrize(
    ("service_id", "status", "http_status"),
    [("auth", "healthy", 200), ("orders", "down", 503), ("notifications", "healthy", 200)],
)
def test_service_health(service_id: str, status: str, http_status: int) -> None:
    response = client.get(f"/api/services/{service_id}/health")
    assert response.status_code == 200
    body = response.json()
    assert (body["service_id"], body["status"], body["http_status"]) == (
        service_id, status, http_status,
    )
    assert body["message"]


def test_order_logs_are_recent_and_show_database_failures() -> None:
    response = client.get("/api/services/orders/logs")
    assert response.status_code == 200
    assert response.json()["service_id"] == "orders"
    logs = response.json()["logs"]
    assert any("pool exhausted" in entry["message"] for entry in logs)
    assert any("database connection timeout" in entry["message"].lower() for entry in logs)
    timestamps = [entry["timestamp"] for entry in logs]
    assert timestamps == sorted(timestamps, reverse=True)
    assert client.get("/api/services/orders/logs?limit=2").json()["logs"] == logs[:2]
    assert client.get("/api/services/orders/logs?limit=100").json()["logs"] == logs


def test_order_metrics_show_exhausted_pool() -> None:
    response = client.get("/api/services/orders/metrics")
    assert response.status_code == 200
    assert response.json() == {
        "service_id": "orders",
        "metrics": {
            "error_rate_percent": 42.0,
            "avg_latency_ms": 2400,
            "requests_last_5m": 120,
            "active_db_connections": 100,
            "db_connection_limit": 100,
        },
    }


@pytest.mark.parametrize("service_id", ["auth", "notifications"])
def test_metrics_omit_database_fields_for_other_services(service_id: str) -> None:
    response = client.get(f"/api/services/{service_id}/metrics")
    assert response.status_code == 200
    assert set(response.json()["metrics"]) == {
        "error_rate_percent", "avg_latency_ms", "requests_last_5m",
    }


@pytest.mark.parametrize("resource", ["health", "logs", "metrics"])
def test_unknown_service(resource: str) -> None:
    response = client.get(f"/api/services/billing/{resource}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Service was not found."}


@pytest.mark.parametrize("limit", ["0", "-1", "101", "not-a-number", "1.5"])
def test_invalid_log_limit(limit: str) -> None:
    response = client.get("/api/services/orders/logs", params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "limit"]


@pytest.mark.parametrize("contents", [None, "{", "{}", '{"orders": {"error_rate_percent": 101}}'])
def test_invalid_or_missing_metrics_fixture(
    contents: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copyfile(services.DATA_DIR / "services.json", tmp_path / "services.json")
    if contents is not None:
        (tmp_path / "metrics.json").write_text(contents, encoding="utf-8")
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    response = client.get("/api/services/orders/metrics")
    assert response.status_code == 503
    assert response.json() == {"detail": "Monitoring data is unavailable (metrics.json)."}


def test_backend_liveness_and_docs() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    assert "/api/services/{id}/health" in client.get("/openapi.json").json()["paths"]
