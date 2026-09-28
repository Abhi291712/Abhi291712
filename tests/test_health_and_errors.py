"""Tests for the health check, the global error format and request ID propagation."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db


def test_health_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_health_reports_database_failure(app, client):
    # Point the session at a database file that cannot be opened.
    broken = sessionmaker(bind=create_engine("sqlite:////nonexistent-dir/x.db"))

    def broken_db():
        session = broken()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = broken_db
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable", "version": "0.1.0"}


def test_unknown_route_uses_standard_error_shape(client):
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


def test_method_not_allowed_uses_standard_error_shape(client):
    response = client.delete("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_response_includes_generated_request_id(client):
    response = client.get("/health")
    assert len(response.headers["X-Request-ID"]) == 32


def test_incoming_request_id_is_echoed(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


def test_unsafe_request_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["X-Request-ID"] != "bad id\nwith newline"


def test_log_lines_carry_request_id(client, caplog):
    caplog.set_level("INFO")

    client.get("/health", headers={"X-Request-ID": "log-check-1"})

    access_lines = [r for r in caplog.records if r.name == "app.access"]
    assert access_lines and all(r.request_id == "log-check-1" for r in access_lines)
