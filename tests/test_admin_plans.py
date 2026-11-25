"""Tests for Dynamic Admin Plan Management.
Verifies plan creation with manual pricing, quotas, dropdown reflection, editing, and safe deletion.
"""
import pytest
from app import app
from models import db, User, Plan
from config import PLANS, PLAN_ORDER

TEST_CSRF = "secure_test_csrf_token_admin_plans"


def get_or_create_user(email, name="Tester", plan="free", is_admin=False):
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


def test_regular_user_cannot_access_admin_plans():
    uid = get_or_create_user("regular_plan_tester@upian.local", name="Regular Tester", is_admin=False)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["csrf"] = TEST_CSRF

    res = client.get("/admin/plans")
    assert res.status_code in (302, 403)


def test_admin_can_access_plans_page():
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    res = client.get("/admin/plans")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Subscription Plans" in html
    assert "Create New Plan" in html


def test_admin_create_plan_with_manual_pricing():
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    form_data = {
        "csrf_token": TEST_CSRF,
        "label": "Enterprise VIP",
        "code": "enterprise_vip",
        "price_inr": "4999",
        "price_usd": "60",
        "period": "/mo",
        "tag": "ENTERPRISE",
        "description": "Dedicated ultra-fast throughput and 24/7 VIP support.",
        "daily": "1000",
        "monthly": "30000",
        "strong_monthly": "5000",
        "image_gen": "1",
        "image_daily": "100",
        "features": "• 1000 daily requests\n• 100 Ultra-HD AI images\n• Priority SLA support",
    }
    res = client.post("/admin/plans/create", data=form_data, follow_redirects=True)
    assert res.status_code == 200

    # Verify plan in database
    with app.app_context():
        plan = Plan.query.filter_by(code="enterprise_vip").first()
        assert plan is not None
        assert plan.label == "Enterprise VIP"
        assert plan.price_inr == 4999.0
        assert plan.price == 60.0
        assert plan.daily == 1000
        assert plan.image_gen is True
        assert plan.image_daily == 100
        assert plan.tag == "ENTERPRISE"
        features = plan.get_features()
        assert len(features) == 3
        assert "1000 daily requests" in features

        # Verify synchronized to in-memory configuration
        assert "enterprise_vip" in PLANS
        assert PLANS["enterprise_vip"]["label"] == "Enterprise VIP"
        assert PLANS["enterprise_vip"]["price_inr"] == 4999.0
        assert "enterprise_vip" in PLAN_ORDER


def test_admin_users_dropdown_reflection():
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    res = client.get("/admin/users")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # The newly created plan should appear in the plan dropdown
    assert "enterprise_vip" in html
    assert "Enterprise VIP" in html
    assert "₹4999" in html


def test_admin_update_plan():
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    with app.app_context():
        plan = Plan.query.filter_by(code="enterprise_vip").first()
        assert plan is not None
        plan_id = plan.id

    form_data = {
        "csrf_token": TEST_CSRF,
        "label": "Enterprise Ultra VIP",
        "price_inr": "5499",
        "price_usd": "65",
        "period": "/year",
        "tag": "ELITE",
        "description": "Updated description with yearly cadence.",
        "daily": "1200",
        "monthly": "40000",
        "strong_monthly": "6000",
        "image_gen": "1",
        "image_daily": "120",
        "features": "• 1200 daily requests\n• 120 Ultra-HD images",
        "is_active": "1",
    }
    res = client.post(f"/admin/plans/{plan_id}/update", data=form_data, follow_redirects=True)
    assert res.status_code == 200

    with app.app_context():
        updated_plan = db.session.get(Plan, plan_id)
        assert updated_plan.label == "Enterprise Ultra VIP"
        assert updated_plan.price_inr == 5499.0
        assert updated_plan.price == 65.0
        assert updated_plan.period == "/year"
        assert updated_plan.tag == "ELITE"

        # Verify memory sync
        assert PLANS["enterprise_vip"]["label"] == "Enterprise Ultra VIP"
        assert PLANS["enterprise_vip"]["price_inr"] == 5499.0


def test_admin_remove_plan():
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    with app.app_context():
        plan = Plan.query.filter_by(code="enterprise_vip").first()
        assert plan is not None
        plan_id = plan.id

    res = client.post(
        f"/admin/plans/{plan_id}/delete",
        data={"csrf_token": TEST_CSRF},
        follow_redirects=True,
    )
    assert res.status_code == 200

    with app.app_context():
        # Verify removed from database
        deleted_plan = db.session.get(Plan, plan_id)
        assert deleted_plan is None

        # Verify removed from in-memory config and order
        assert "enterprise_vip" not in PLANS
        assert "enterprise_vip" not in PLAN_ORDER


def test_system_plan_protected_from_deletion():
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    with app.app_context():
        free_plan = Plan.query.filter_by(code="free").first()
        assert free_plan is not None
        free_id = free_plan.id

    res = client.post(
        f"/admin/plans/{free_id}/delete",
        data={"csrf_token": TEST_CSRF},
        follow_redirects=True,
    )
    assert res.status_code == 200

    with app.app_context():
        # Free plan must still exist
        assert db.session.get(Plan, free_id) is not None
        assert "free" in PLANS
        assert "free" in PLAN_ORDER


def test_back_to_chat_navigation_buttons():
    """Verify that 'Back to Chat' button is prominent and present across Admin, Files, Activity, and Plans."""
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    # 1. Admin Plans Page
    res_admin = client.get("/admin/plans")
    assert res_admin.status_code == 200
    html_admin = res_admin.get_data(as_text=True)
    assert "Back to Chat" in html_admin
    assert "btn-head-back-chat" in html_admin
    assert "btn-side-return-chat" not in html_admin
    assert "btn-back-chat-pill" not in html_admin

    # 2. Files Page
    res_files = client.get("/files")
    assert res_files.status_code == 200
    html_files = res_files.get_data(as_text=True)
    assert "Back to Chat" in html_files
    assert "btn-head-back-chat" in html_files

    # 3. Activity Page
    res_act = client.get("/activity")
    assert res_act.status_code == 200
    html_act = res_act.get_data(as_text=True)
    assert "Back to Chat" in html_act
    assert "btn-head-back-chat" in html_act

    # 4. Plans Page
    res_plans = client.get("/plans")
    assert res_plans.status_code == 200
    html_plans = res_plans.get_data(as_text=True)
    assert "Back to Chat" in html_plans
    assert "btn-head-back-chat" in html_plans


def test_admin_created_plan_instantly_visible_to_regular_user():
    """Verify that when an admin creates a plan, it is immediately rendered for regular users in /plans and /api/payment/plans."""
    admin_id = get_or_create_user("admin_plan_tester@upian.local", name="Admin Tester", is_admin=True)
    user_id = get_or_create_user("user_viewer@upian.local", name="Viewer Tester", is_admin=False)

    admin_client = app.test_client()
    with admin_client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    # Admin creates new plan
    plan_data = {
        "csrf_token": TEST_CSRF,
        "label": "Titanium Suite",
        "code": "titanium_suite",
        "price_inr": "5999",
        "price_usd": "72",
        "price_inr_yearly": "59990",
        "price_usd_yearly": "720",
        "period": "/mo",
        "description": "Enterprise-grade quantum compute limits",
        "tag": "TITANIUM",
        "daily": "1000",
        "monthly": "25000",
        "strong_monthly": "5000",
        "image_gen": "on",
        "image_daily": "100",
        "features": "Dedicated AI clusters\n100 AI Images/Day\n24/7 dedicated engineer",
    }
    create_res = admin_client.post("/admin/plans/create", data=plan_data, follow_redirects=True)
    assert create_res.status_code == 200

    # Regular user visits /plans
    user_client = app.test_client()
    with user_client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = TEST_CSRF

    plans_res = user_client.get("/plans")
    assert plans_res.status_code == 200
    plans_html = plans_res.get_data(as_text=True)
    assert "Titanium Suite" in plans_html
    assert "5999" in plans_html
    assert "TITANIUM" in plans_html

    # Check API returns it as well
    api_res = user_client.get("/api/payment/plans")
    assert api_res.status_code == 200
    api_data = api_res.get_json()
    assert any(p["id"] == "titanium_suite" for p in api_data["plans"])

    # Clean up: Admin deletes plan
    with app.app_context():
        p_obj = Plan.query.filter_by(code="titanium_suite").first()
        assert p_obj is not None
        p_id = p_obj.id

    del_res = admin_client.post(f"/admin/plans/{p_id}/delete", data={"csrf_token": TEST_CSRF}, follow_redirects=True)
    assert del_res.status_code == 200

    # User re-checks: plan must be gone
    plans_res2 = user_client.get("/plans")
    plans_html2 = plans_res2.get_data(as_text=True)
    assert "Titanium Suite" not in plans_html2
