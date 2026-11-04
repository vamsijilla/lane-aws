import pytest
from fastapi.testclient import TestClient

from lane.api.main import create_app


def test_health_reports_database_and_version() -> None:
    client = TestClient(create_app())
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_health_reports_missing_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    client = TestClient(create_app())
    assert client.get("/api/v1/health").json()["llm"] == "missing_key"


def test_health_reports_configured_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = TestClient(create_app())
    assert client.get("/api/v1/health").json()["llm"] == "configured"
