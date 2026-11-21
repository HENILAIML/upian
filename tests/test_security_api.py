"""Unit and Security Tests for UPIAN AI Gateway (Phase 9)."""
import pytest
from app import app
from models import User, db


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_healthz_endpoint(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "classifier" in data


def test_vision_models_endpoint(client):
    res = client.get("/api/vision/models")
    assert res.status_code == 200
    data = res.get_json()
    assert "models" in data
    model_ids = [m["id"] for m in data["models"]]
    assert "mobilenet_v3_small" in model_ids
    assert "siglip2_base" in model_ids


def test_image_models_endpoint(client):
    res = client.get("/api/image/models")
    assert res.status_code == 200
    data = res.get_json()
    assert "providers" in data
    assert "low_cost_model" in data
    assert "premium_model" in data


def test_unauthenticated_api_rejection(client):
    res = client.get("/api/activity")
    assert res.status_code == 401
    assert "session" in res.get_json()["error"].lower()


def test_csrf_protection_on_post(client):
    # Attempt POST without CSRF token or session
    res = client.post("/api/chat", json={"prompt": "Hello"})
    # Must reject with 400 Bad Request due to missing CSRF token
    assert res.status_code in (400, 401)


def test_path_traversal_rejection(client):
    # Malformed download token
    res = client.get("/api/image/download/../../etc/passwd")
    assert res.status_code in (400, 404)

    res_extract = client.get("/api/extract/download/../../malicious/file.txt")
    assert res_extract.status_code in (400, 404)


def test_404_error_handler(client):
    res = client.get("/api/nonexistent-route-xyz")
    assert res.status_code == 404
    assert res.is_json
    assert "error" in res.get_json()
