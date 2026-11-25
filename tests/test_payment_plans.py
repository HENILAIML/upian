"""Unit and Integration Tests for 3 Plans & Payment Gateway."""
import pytest
from app import app
from models import db, User, Payment
from config import PLANS, PLAN_ORDER

TEST_CSRF = "secure_test_csrf_token_12345"


@pytest.fixture
def auth_client():
    app.config["TESTING"] = True
    with app.app_context():
        user = User.query.filter_by(email="test_subscriber@upian.local").first()
        if not user:
            user = User(name="Test Subscriber", email="test_subscriber@upian.local", plan="free")
            user.set_password("TestPass@123")
            db.session.add(user)
            db.session.commit()
        else:
            user.plan = "free"
            db.session.commit()
        user_id = user.id

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["uid"] = user_id
            sess["csrf"] = TEST_CSRF
        yield client


def test_plans_configuration():
    assert "free" in PLANS
    assert "plus" in PLANS
    assert "plus_pro" in PLANS
    assert PLANS["free"]["price"] == 0
    assert PLANS["plus"]["label"] == "Pro"
    assert PLANS["plus"]["price"] == 15
    assert PLANS["plus"]["image_gen"] is True
    assert PLANS["plus_pro"]["label"] == "Pro Plus"
    assert PLANS["plus_pro"]["price"] == 39
    assert PLANS["plus_pro"]["image_gen"] is True
    assert PLANS["free"]["image_daily"] == 0
    assert PLANS["plus"]["image_daily"] == 20
    assert PLANS["plus_pro"]["image_daily"] == 30


def test_api_payment_plans_endpoint(auth_client):
    res = auth_client.get("/api/payment/plans")
    assert res.status_code == 200
    data = res.get_json()
    assert "plans" in data
    assert len(data["plans"]) == 3
    plan_labels = [p["label"] for p in data["plans"]]
    assert "Free" in plan_labels
    assert "Pro" in plan_labels
    assert "Pro Plus" in plan_labels


def test_payment_process_upgrade_to_pro(auth_client):
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    res = auth_client.post(
        "/api/payment/process",
        headers=headers,
        json={
            "plan": "plus",
            "payment_method": "card",
            "amount": 15.0,
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["plan"] == "plus"
    assert data["plan_label"] == "Pro"
    assert "transaction_id" in data
    assert data["transaction_id"].startswith("UPIAN-PAY-")

    with app.app_context():
        user = User.query.filter_by(email="test_subscriber@upian.local").first()
        assert user.plan == "plus"
        pay = Payment.query.filter_by(transaction_id=data["transaction_id"]).first()
        assert pay is not None
        assert pay.amount == 15.0
        assert pay.status == "completed"


def test_payment_process_upgrade_to_pro_plus(auth_client):
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    res = auth_client.post(
        "/api/payment/process",
        headers=headers,
        json={
            "plan": "plus_pro",
            "payment_method": "upi",
            "amount": 39.0,
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["plan"] == "plus_pro"
    assert data["plan_label"] == "Pro Plus"

    with app.app_context():
        user = User.query.filter_by(email="test_subscriber@upian.local").first()
        assert user.plan == "plus_pro"


def test_free_plan_blocks_image_generation_and_requires_upgrade(auth_client):
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    # Switch back to free
    auth_client.post("/api/payment/downgrade", headers=headers, json={})

    # Send image generation prompt via chat stream
    res = auth_client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": "Generate an image of a cybernetic tiger"},
    )
    assert res.status_code == 200
    text_data = res.get_data(as_text=True)
    # Free users are blocked from image generation with upgrade notice
    assert "upgrade_required" in text_data or "Free users cannot generate" in text_data


def test_custom_checkbox_feature_selection(auth_client):
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    custom_features = ["core", "image_gen", "reasoning", "ocr_pdf"]
    res = auth_client.post(
        "/api/payment/process",
        headers=headers,
        json={
            "plan": "custom",
            "custom_features": custom_features,
            "amount": 747.0,
            "payment_method": "upi",
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["is_custom"] is True
    assert data["amount"] == 747.0
    assert data["custom_features"] == custom_features
    assert "Custom" in data["plan_label"]

    with app.app_context():
        user = User.query.filter_by(email="test_subscriber@upian.local").first()
        assert user.plan in ("plus", "plus_pro")
        pay = Payment.query.filter_by(transaction_id=data["transaction_id"]).first()
        assert pay is not None
        assert pay.amount == 747.0
        assert pay.status == "completed"


def test_plans_page_renders_with_checkboxes(auth_client):
    """Test that the plans page loads clean subscription tiers with Back to Chat and without custom builder."""
    res = auth_client.get("/plans")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Back to Chat" in html
    assert "Choose Your Intelligence Tier" in html
    assert "page-card-free" in html or "Free" in html
    assert "page-card-plus" in html or "Pro" in html
    assert "Build Your Own Custom Plan" not in html


def test_custom_plan_activates_image_generation_and_reasoning(auth_client):
    """Test that selecting image_gen in custom plan enables image generation capability."""
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    # Downgrade first to free
    auth_client.post("/api/payment/downgrade", headers=headers, json={})

    # Upgrade via custom checkbox selection with image_gen
    res = auth_client.post(
        "/api/payment/process",
        headers=headers,
        json={
            "plan": "custom",
            "custom_features": ["core", "image_gen"],
            "amount": 598.0,
            "payment_method": "card",
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    # User's plan must be upgraded so image generation is allowed
    with app.app_context():
        user = User.query.filter_by(email="test_subscriber@upian.local").first()
        assert user.plan in ("plus", "plus_pro", "custom")
        plan_info = PLANS.get(user.plan, PLANS["free"])
        assert plan_info.get("image_gen") is True
