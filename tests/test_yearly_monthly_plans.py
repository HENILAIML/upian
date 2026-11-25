"""Unit and Integration Tests for Monthly and Yearly Subscription Plans.
Verifies:
1. Model & database support for monthly & yearly pricing.
2. Admin ability to set and update monthly and yearly pricing.
3. Real-time reflection of admin price changes in user-facing endpoints.
4. User subscription purchase for monthly (30 days) and yearly (365 days).
5. Razorpay order generation honoring monthly vs yearly billing cycles.
"""
from datetime import datetime, timezone
import pytest
from app import app
from models import db, User, Plan, Payment
from config import PLANS, PLAN_ORDER

TEST_CSRF = "secure_test_csrf_token_yearly_monthly_123"


def get_or_create_user(email, name="Plan User", plan="free", is_admin=False):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=name, email=email, plan=plan, is_admin=is_admin)
            user.set_password("SecurePass@123")
            db.session.add(user)
            db.session.commit()
        else:
            user.is_admin = is_admin
            user.plan = plan
            db.session.commit()
        return user.id


@pytest.fixture
def user_client():
    app.config["TESTING"] = True
    uid = get_or_create_user("subscriber_yearly_tester@upian.local", name="Subscriber Tester", plan="free", is_admin=False)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["csrf"] = TEST_CSRF
    yield client, uid


@pytest.fixture
def admin_client():
    app.config["TESTING"] = True
    admin_id = get_or_create_user("admin_yearly_tester@upian.local", name="Admin Yearly Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF
    yield client


def test_plan_model_and_config_yearly_pricing():
    with app.app_context():
        plus_plan = Plan.query.filter_by(code="plus").first()
        assert plus_plan is not None
        assert plus_plan.price_inr > 0
        assert plus_plan.price_inr_yearly > 0

        p_dict = plus_plan.to_dict()
        assert "price_yearly" in p_dict
        assert "price_inr_yearly" in p_dict
        assert "price_yearly_str" in p_dict
        assert "price_inr_yearly_str" in p_dict

        plan_dict = plus_plan.to_plan_dict()
        assert "price_inr_yearly" in plan_dict
        assert plan_dict["price_inr_yearly"] > 0


def test_api_payment_plans_endpoint_returns_monthly_and_yearly(user_client):
    client, _ = user_client
    res = client.get("/api/payment/plans")
    assert res.status_code == 200
    data = res.get_json()
    assert "plans" in data

    pro_plan = next((p for p in data["plans"] if p["id"] in ("plus", "pro")), None)
    assert pro_plan is not None
    assert "price" in pro_plan
    assert "price_inr" in pro_plan
    assert "price_yearly" in pro_plan
    assert "price_inr_yearly" in pro_plan
    assert pro_plan["price_inr_yearly"] > pro_plan["price_inr"]


def test_user_buys_monthly_plan(user_client):
    client, uid = user_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}

    payload = {
        "plan": "pro",
        "billing_cycle": "monthly",
        "payment_method": "card",
    }
    res = client.post("/api/payment/process", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["billing_cycle"] == "monthly"
    assert data["duration_days"] == 30

    with app.app_context():
        user = db.session.get(User, uid)
        assert user.plan == "plus"
        assert user.plan_status == "active"
        # duration should be around 30 days
        diff_days = (user.plan_end - user.plan_start).days
        assert diff_days in (29, 30, 31)

        payment = Payment.query.filter_by(user_id=uid).order_by(Payment.id.desc()).first()
        assert payment is not None
        assert payment.amount == PLANS["plus"]["price_inr"]


def test_user_buys_yearly_plan(user_client):
    client, uid = user_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}

    payload = {
        "plan": "pro",
        "billing_cycle": "yearly",
        "payment_method": "card",
    }
    res = client.post("/api/payment/process", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["billing_cycle"] == "yearly"
    assert data["duration_days"] == 365

    with app.app_context():
        user = db.session.get(User, uid)
        assert user.plan == "plus"
        assert user.plan_status == "active"
        # duration should be around 365 days
        diff_days = (user.plan_end - user.plan_start).days
        assert diff_days in (364, 365, 366)

        payment = Payment.query.filter_by(user_id=uid).order_by(Payment.id.desc()).first()
        assert payment is not None
        assert payment.amount == PLANS["plus"]["price_inr_yearly"]


def test_razorpay_order_honors_yearly_billing(user_client):
    client, _ = user_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}

    # 1. Monthly Order
    res_m = client.post("/api/payment/razorpay/order", headers=headers, json={"plan": "pro", "billing_cycle": "monthly"})
    assert res_m.status_code == 200
    data_m = res_m.get_json()
    assert data_m["success"] is True
    assert data_m["billing_cycle"] == "monthly"
    assert data_m["amount"] == PLANS["plus"]["price_inr"]

    # 2. Yearly Order
    res_y = client.post("/api/payment/razorpay/order", headers=headers, json={"plan": "pro", "billing_cycle": "yearly"})
    assert res_y.status_code == 200
    data_y = res_y.get_json()
    assert data_y["success"] is True
    assert data_y["billing_cycle"] == "yearly"
    assert data_y["amount"] == PLANS["plus"]["price_inr_yearly"]


def test_admin_creates_and_updates_plan_with_both_prices(admin_client, user_client):
    form_data = {
        "csrf_token": TEST_CSRF,
        "label": "Growth Business",
        "code": "growth_biz",
        "price_inr": "1499",
        "price_usd": "18",
        "price_inr_yearly": "14990",
        "price_usd_yearly": "180",
        "period": "/mo",
        "tag": "GROWTH",
        "description": "Designed for expanding business operations.",
        "daily": "500",
        "monthly": "15000",
        "strong_monthly": "1500",
        "image_gen": "1",
        "image_daily": "50",
        "features": "• 500 requests/day\n• 50 AI Images/day",
    }
    res = admin_client.post("/admin/plans/create", data=form_data, follow_redirects=True)
    assert res.status_code == 200

    with app.app_context():
        plan = Plan.query.filter_by(code="growth_biz").first()
        assert plan is not None
        assert plan.price_inr == 1499.0
        assert plan.price_inr_yearly == 14990.0
        assert plan.price == 18.0
        assert plan.price_yearly == 180.0
        plan_id = plan.id

    # Verify admin plans table displays yearly price
    res_adm = admin_client.get("/admin/plans")
    assert res_adm.status_code == 200
    html_adm = res_adm.get_data(as_text=True)
    assert "₹14990" in html_adm
    assert "Growth Business" in html_adm

    # Verify user payment plans endpoint returns the newly created plan with both prices
    client_user, _ = user_client
    res_u = client_user.get("/api/payment/plans")
    assert res_u.status_code == 200
    data_u = res_u.get_json()
    biz_plan = next((p for p in data_u["plans"] if p["id"] == "growth_biz"), None)
    assert biz_plan is not None
    assert biz_plan["price_inr"] == 1499.0
    assert biz_plan["price_inr_yearly"] == 14990.0

    # Clean up test plan
    res_del = admin_client.post(f"/admin/plans/{plan_id}/delete", data={"csrf_token": TEST_CSRF}, follow_redirects=True)
    assert res_del.status_code == 200


def test_admin_price_change_updates_razorpay_order_immediately(admin_client, user_client):
    """Test that when an admin updates the Pro plan price from 999 to 1999,
    Razorpay order creation immediately uses 1999 instead of 999.
    """
    client_user, _ = user_client
    headers = {"X-CSRF-Token": TEST_CSRF, "Content-Type": "application/json"}

    with app.app_context():
        pro_plan = Plan.query.filter(Plan.code.in_(["plus", "pro"])).first()
        assert pro_plan is not None
        orig_price = pro_plan.price
        orig_price_inr = pro_plan.price_inr
        orig_price_yr = pro_plan.price_yearly
        orig_price_yr_inr = pro_plan.price_inr_yearly
        orig_image_gen = pro_plan.image_gen
        pro_id = pro_plan.id

    try:
        # 1. Update Pro plan price to 1999 via admin endpoint
        update_data = {
            "csrf_token": TEST_CSRF,
            "label": "Pro",
            "price_inr": "1999",
            "price_usd": "15",
            "price_inr_yearly": "19990",
            "price_usd_yearly": "150",
            "daily": "100",
            "monthly": "2000",
            "strong_monthly": "400",
            "image_gen": "1",
            "image_daily": "20",
            "is_active": "1",
        }
        res_up = admin_client.post(f"/admin/plans/{pro_id}/update", data=update_data, follow_redirects=True)
        assert res_up.status_code == 200

        # 2. Verify Razorpay order endpoint creates order with updated amount 1999
        res_ord = client_user.post("/api/payment/razorpay/order", headers=headers, json={"plan": "pro", "billing_cycle": "monthly"})
        assert res_ord.status_code == 200
        ord_data = res_ord.get_json()
        assert ord_data["success"] is True
        assert ord_data["amount"] == 1999.0

        # 3. Verify user payment plans endpoint returns 1999
        res_plans = client_user.get("/api/payment/plans")
        assert res_plans.status_code == 200
        plans_data = res_plans.get_json()
        pro_info = next((p for p in plans_data["plans"] if p["id"] in ("plus", "pro")), None)
        assert pro_info is not None
        assert pro_info["price_inr"] == 1999.0

    finally:
        # Restore original prices and attributes
        with app.app_context():
            p = db.session.get(Plan, pro_id)
            p.price = orig_price
            p.price_inr = orig_price_inr
            p.price_yearly = orig_price_yr
            p.price_inr_yearly = orig_price_yr_inr
            p.image_gen = orig_image_gen
            db.session.commit()
            import plans_service
            plans_service.sync_plans_to_memory()
