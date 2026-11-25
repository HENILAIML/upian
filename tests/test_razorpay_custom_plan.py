import pytest
from app import app, db
from models import User, Payment

TEST_CSRF = "secure_test_csrf_token_razorpay_9999"

@pytest.fixture
def auth_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with app.app_context():
            user = User.query.filter_by(email="razorpay_tester@upian.local").first()
            if not user:
                user = User(name="Razorpay Tester", email="razorpay_tester@upian.local", plan="free")
                db.session.add(user)
                db.session.commit()
            uid = user.id

        with client.session_transaction() as sess:
            sess["uid"] = uid
            sess["csrf"] = TEST_CSRF

        yield client, uid


def test_razorpay_order_custom_plan(auth_client):
    client, uid = auth_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    
    # 1. Test Razorpay Order creation with custom plan
    payload = {
        "plan": "custom",
        "custom_plan_name": "Gold VIP Plan",
        "amount": 799
    }
    res = client.post("/api/payment/razorpay/order", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["key_id"] == "rzp_test_U3UJqnIOtuC4p4"
    assert data["amount"] == 799.0
    assert data["plan_label"] == "Gold VIP Plan"
    assert "order_id" in data


def test_custom_plan_payment_process_and_save(auth_client):
    client, uid = auth_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    
    # 2. Test Custom Plan payment process and name saving
    payload = {
        "plan": "custom",
        "custom_plan_name": "Gold VIP Plan",
        "custom_features": ["core", "reasoning", "image_gen", "Watermark Remover", "Dedicated Support"],
        "amount": 799,
        "payment_method": "razorpay"
    }
    res = client.post("/api/payment/process", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["plan_label"] == "Gold VIP Plan"
    assert data["is_custom"] is True
    assert "TX:" in data["transaction_id"] or "UPIAN-PAY-" in data["transaction_id"]

    # 3. Verify user in database
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.plan == "plus"
        assert user.subscription_id == "custom:Gold VIP Plan"
        
        # Verify payment table
        payment = Payment.query.filter_by(user_id=uid).order_by(Payment.id.desc()).first()
        assert payment is not None
        assert payment.amount == 799.0
        assert payment.status == "completed"


def test_ai_response_no_broken_banner(auth_client):
    client, uid = auth_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}
    
    # Test chat endpoint with general prompt
    res = client.post("/api/chat", headers=headers, json={"prompt": "Hello Upian, tell me who you are"})
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "API Key Notice" not in text
    assert "[Mock AI Provider:" not in text
    assert "Upian AI" in text
