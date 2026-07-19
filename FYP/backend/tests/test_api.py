"""
ANPR API smoke tests — run with:
    cd FYP/backend
    pytest tests/ -v
Requires: pip install pytest httpx pytest-asyncio
"""
import os
import sys
import pytest

# conftest.py handles env setup and camera stub
from fastapi.testclient import TestClient
from main import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _login() -> str:
    """Return a valid JWT token for the admin user (skips if DB unavailable)."""
    r = client.post(
        "/api/auth/login/json",
        json={"username": os.environ["ADMIN_USERNAME"],
              "password": os.environ["ADMIN_PASSWORD"]},
    )
    if r.status_code == 503:
        pytest.skip("Database unavailable — skipping authenticated tests")
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Health ────────────────────────────────────────────────────────────────────

def test_root_returns_200():
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert "system" in data
    assert data["system"] == "ANPR"


def test_health_endpoint_shape():
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "camera_worker" in data
    assert "database" in data


def test_docs_accessible():
    r = client.get("/api/docs")
    assert r.status_code == 200


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_login_wrong_password_returns_401():
    r = client.post(
        "/api/auth/login/json",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert r.status_code in (401, 503)  # 503 if DB is down


def test_login_empty_body_returns_422():
    r = client.post("/api/auth/login/json", json={})
    assert r.status_code == 422


def test_protected_route_without_token_returns_401():
    r = client.get("/api/stats")
    assert r.status_code == 401


def test_protected_route_with_bad_token_returns_401():
    r = client.get("/api/stats", headers={"Authorization": "Bearer bad.token.here"})
    assert r.status_code == 401


def test_login_and_me():
    token = _login()
    r = client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == os.environ["ADMIN_USERNAME"]
    assert data["role"] == "admin"


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_shape():
    token = _login()
    r = client.get("/api/stats", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    for key in ("total_vehicles", "authorized_vehicles", "unauthorized_vehicles",
                "total_detections_today", "authorized_today", "unauthorized_today",
                "total_detections_all"):
        assert key in data, f"Missing key: {key}"
        assert isinstance(data[key], int)


# ── Vehicles ──────────────────────────────────────────────────────────────────

def test_vehicles_list_shape():
    token = _login()
    r = client.get("/api/vehicles?page=1&per_page=5", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data
    assert "pages" in data
    assert isinstance(data["items"], list)


def test_vehicles_pagination_bounds():
    token = _login()
    # per_page > 100 should be rejected
    r = client.get("/api/vehicles?per_page=999", headers=_auth(token))
    assert r.status_code == 422


def test_vehicle_create_invalid_plate_returns_400():
    token = _login()
    r = client.post(
        "/api/vehicles",
        json={"license_number": "   ", "dues": "Clear", "status": "Authorized"},
        headers=_auth(token),
    )
    assert r.status_code in (400, 422)


def test_vehicle_not_found_returns_404():
    token = _login()
    r = client.get("/api/vehicles/999999999", headers=_auth(token))
    assert r.status_code == 404


# ── Detections ────────────────────────────────────────────────────────────────

def test_detections_list_shape():
    token = _login()
    r = client.get("/api/detections?page=1&per_page=5", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_detections_invalid_date_returns_400():
    token = _login()
    r = client.get("/api/detections?date_from=not-a-date", headers=_auth(token))
    assert r.status_code == 400


def test_detection_delete_nonexistent_returns_404():
    token = _login()
    r = client.delete("/api/detections/999999999", headers=_auth(token))
    assert r.status_code == 404


# ── Alerts ────────────────────────────────────────────────────────────────────

def test_alerts_list_shape():
    token = _login()
    r = client.get("/api/alerts?page=1&per_page=5", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert "items" in data


# ── Body size limit ───────────────────────────────────────────────────────────

def test_oversized_body_returns_413():
    token = _login()
    big_payload = {"license_number": "A" * (3 * 1024 * 1024)}  # 3 MB
    r = client.post("/api/vehicles", json=big_payload, headers=_auth(token))
    assert r.status_code == 413


# ── WebSocket ticket ──────────────────────────────────────────────────────────

def test_stream_ticket_requires_auth():
    r = client.post("/api/stream/ticket")
    assert r.status_code == 401


def test_stream_ticket_issued_for_authed_user():
    token = _login()
    r = client.post("/api/stream/ticket", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert "ticket" in data
    assert len(data["ticket"]) > 10


# ── Password change ───────────────────────────────────────────────────────────

def test_change_password_wrong_current_returns_400():
    token = _login()
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "WrongPass@999!", "new_password": "NewPass@Secure2024!"},
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_change_password_same_as_current_returns_400():
    token = _login()
    current = os.environ["ADMIN_PASSWORD"]
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": current, "new_password": current},
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_change_password_weak_new_returns_400():
    token = _login()
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": os.environ["ADMIN_PASSWORD"], "new_password": "weak"},
        headers=_auth(token),
    )
    assert r.status_code == 400


# ── RBAC ─────────────────────────────────────────────────────────────────────

def test_vehicle_create_requires_admin():
    """Non-admin token (if any) must be rejected; unauthenticated always 401."""
    r = client.post("/api/vehicles", json={"license_number": "ABC-1234", "dues": "Clear", "status": "Authorized"})
    assert r.status_code == 401


def test_vehicle_delete_requires_admin():
    r = client.delete("/api/vehicles/1")
    assert r.status_code == 401


def test_reload_store_requires_admin():
    r = client.post("/api/stats/reload-store")
    assert r.status_code == 401


# ── CSV Export ────────────────────────────────────────────────────────────────

def test_export_requires_auth():
    r = client.get("/api/detections/export")
    assert r.status_code == 401


def test_export_returns_csv():
    token = _login()
    r = client.get("/api/detections/export", headers=_auth(token))
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    # First line must be the CSV header
    first_line = r.text.splitlines()[0] if r.text else ""
    assert "detected_plate" in first_line


def test_export_with_filters():
    token = _login()
    r = client.get(
        "/api/detections/export?status=authorized&date_from=2024-01-01",
        headers=_auth(token),
    )
    assert r.status_code == 200


def test_export_invalid_date_returns_400():
    token = _login()
    r = client.get("/api/detections/export?date_from=not-a-date", headers=_auth(token))
    assert r.status_code == 400
