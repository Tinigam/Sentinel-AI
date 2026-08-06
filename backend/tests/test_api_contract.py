from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


class FakeSession:
    def execute(self, _statement: object) -> None:
        return None


def override_db():
    yield FakeSession()


def test_health_endpoint_returns_database_status() -> None:
    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "version": "0.1.0"}


def test_openapi_exposes_v1_core_routes() -> None:
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/health"]
    assert "get" in paths["/api/v1/news"]
    assert "post" in paths["/api/v1/ask"]
    assert "post" in paths["/api/v1/ingest"]