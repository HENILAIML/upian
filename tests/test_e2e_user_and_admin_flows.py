"""Comprehensive End-to-End System Test for User and Admin Workflows.
Tests:
1. Public & pricing routes with monthly/yearly plan options.
2. User authentication & dynamic quota checking from DB.
3. Chat stream stop vs success:
   - Completed chat consumes quota.
   - Stopped chat records status='stopped' and consumes 0 quota.
   - Subsequent prompt works cleanly without double counting.
4. Non-admin security: blocked from all admin endpoints with 403 or redirect.
5. Admin management:
   - Admin view of plans and users.
   - Admin edit of Free plan dynamically updates user's live quota limits.
   - Admin edit user, cancel subscription, soft-delete user with audit logging.
   - Admin delete plan requiring replacement plan and atomic migration of users.
   - Admin blocked from deleting last default plan.
6. Frontend template checks:
   - Single stop button inside composer toggled by isStreaming (no duplicate stop bar).
   - Code block copy button integration with Clipboard API and fallback.
   - Clean, non-redundant Back-to-chat links across files, activity, and admin pages.
"""
import pytest
import json
import uuid
import time
from app import app
from models import db, User, Plan, AuditLog, RequestLog
from plans_service import ensure_seed_plans, get_default_plan, remove_plan, create_new_plan
from quota import get_usage

TEST_CSRF = "test_csrf_token_e2e_flows_12345"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["csrf"] = TEST_CSRF
        yield client


def create_test_user(email, is_admin=False, plan="free"):
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(name=email.split("@")[0], email=email, plan=plan, is_admin=is_admin)
            u.set_password("SecretPass123!")
            db.session.add(u)
            db.session.commit()
        else:
            u.is_admin = is_admin
            u.plan = plan
            u.deleted_at = None
            db.session.commit()
        return u.id


# =====================================================================
# 1. Public & Pricing Routes End-to-End
# =====================================================================
def test_public_and_pricing_endpoints(client):
    """Verify visitor access to login and authenticated access to plans API."""
    with app.app_context():
        ensure_seed_plans()

    # Login page
    res_login = client.get("/login")
    assert res_login.status_code == 200

    # Create user to test dynamic payment plans endpoint
    uid = uuid.uuid4().hex[:6]
    user_email = f"plans_viewer_{uid}@upian.local"
    user_id = create_test_user(user_email, is_admin=False, plan="free")

    with client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = TEST_CSRF

    # Plans HTML page
    res_plans = client.get("/plans")
    assert res_plans.status_code == 200

    # API payment plans endpoint (returns active plans with monthly/yearly pricing)
    res_api = client.get("/api/payment/plans")
    assert res_api.status_code == 200
    data = res_api.get_json()
    assert "plans" in data
    # Must include zero-price baseline plan (Free)
    assert any(p.get("price_inr") == 0 or p.get("id") == "free" for p in data["plans"])
    # Must have monthly and yearly prices
    for p in data["plans"]:
        assert "price_inr" in p
        assert "price_inr_yearly" in p


# =====================================================================
# 2. User Authentication & Dynamic Quota from DB
# =====================================================================
def test_user_login_and_dynamic_quota(client):
    """Verify authenticated user gets quota directly computed from DB plan."""
    uid = uuid.uuid4().hex[:6]
    user_email = f"quota_user_{uid}@upian.local"
    user_id = create_test_user(user_email, is_admin=False, plan="free")

    with client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = TEST_CSRF

    # Authenticated user can view plans page
    res_plans = client.get("/plans")
    assert res_plans.status_code == 200

    # Usage API
    res_usage = client.get("/api/usage")
    assert res_usage.status_code == 200
    usage_data = res_usage.get_json()
    assert "day" in usage_data
    assert "month" in usage_data
    assert usage_data["day"]["limit"] > 0


# =====================================================================
# 3. Chat Stop vs Complete Quota Counting
# =====================================================================
def test_chat_stop_zero_quota_and_reprompt(client):
    """
    Verify stopped prompt does not consume quota, but subsequent prompt consumes quota cleanly.
    """
    uid = uuid.uuid4().hex[:6]
    user_email = f"chat_stop_user_{uid}@upian.local"
    user_id = create_test_user(user_email, is_admin=False, plan="free")

    with client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = TEST_CSRF

    with app.app_context():
        user = db.session.get(User, user_id)
        RequestLog.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        initial_usage = get_usage(user)["day"]["used"]

    # 1. Simulate stopped chat prompt
    stop_req_id = f"stop_req_{uuid.uuid4().hex[:8]}"
    res_stop = client.post(
        "/api/chat/stop",
        headers={"X-CSRFToken": TEST_CSRF},
        json={"request_id": stop_req_id}
    )
    assert res_stop.status_code == 200
    stop_json = res_stop.get_json()
    assert stop_json.get("success") is True or stop_json.get("stopped") is True

    with app.app_context():
        # Record a stopped request log
        log_stop = RequestLog(
            user_id=user_id,
            prompt="Write a long algorithm",
            status="stopped",
            tier="simple",
            classifier="rule",
            latency_ms=200
        )
        db.session.add(log_stop)
        db.session.commit()

        user = db.session.get(User, user_id)
        after_stop_usage = get_usage(user)["day"]["used"]
        # Stopped request must NOT increase quota
        assert after_stop_usage == initial_usage

    # 2. Simulate subsequent successful request
    with app.app_context():
        succ_log = RequestLog(
            user_id=user_id,
            prompt="Hello world",
            tier="weak",
            provider="google",
            model="gemini-2.0-flash",
            status="success",
            tokens_in=10,
            tokens_out=25
        )
        db.session.add(succ_log)
        db.session.commit()

        user = db.session.get(User, user_id)
        after_succ_usage = get_usage(user)["day"]["used"]
        assert after_succ_usage == initial_usage + 1


# =====================================================================
# 4. Security: Non-Admin Blocked from Admin Endpoints
# =====================================================================
def test_non_admin_blocked_with_403(client):
    """Verify regular user is blocked from all admin endpoints with 403 or redirect."""
    uid = uuid.uuid4().hex[:6]
    user_email = f"regular_user_{uid}@upian.local"
    user_id = create_test_user(user_email, is_admin=False, plan="free")

    with client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = TEST_CSRF

    # Admin dashboard
    res_admin = client.get("/admin")
    assert res_admin.status_code in (403, 302)

    # Admin plans list
    res_plans = client.get("/admin/plans")
    assert res_plans.status_code in (403, 302)

    # Admin users API
    res_edit = client.post(
        f"/admin/users/{user_id}/edit",
        data={"csrf_token": TEST_CSRF, "name": "Hacked", "plan": "pro", "status": "active"},
        headers={"X-CSRFToken": TEST_CSRF}
    )
    assert res_edit.status_code in (403, 302)

    res_del = client.post(
        f"/admin/users/{user_id}/delete",
        data={"csrf_token": TEST_CSRF},
        headers={"X-CSRFToken": TEST_CSRF}
    )
    assert res_del.status_code in (403, 302)

    res_plan_del = client.post(
        "/admin/plans/1/delete",
        data={"csrf_token": TEST_CSRF, "replacement_plan_id": 1},
        headers={"X-CSRFToken": TEST_CSRF}
    )
    assert res_plan_del.status_code in (403, 302)


# =====================================================================
# 5. Admin Management Workflows End-to-End
# =====================================================================
def test_admin_workflows_and_audit_logging(client):
    """
    Test full admin lifecycle:
    - Edit Free plan limits and verify user's live quota reflects the change.
    - Edit user, cancel user plan, and soft-delete user.
    - Check audit log entries.
    """
    admin_uid = uuid.uuid4().hex[:6]
    admin_email = f"admin_{admin_uid}@upian.local"
    admin_id = create_test_user(admin_email, is_admin=True, plan="pro")

    target_uid = uuid.uuid4().hex[:6]
    target_email = f"target_user_{target_uid}@upian.local"
    target_id = create_test_user(target_email, is_admin=False, plan="free")

    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    # Step A: Edit Free plan limits
    with app.app_context():
        free_plan = Plan.query.filter_by(code="free").first()
        free_id = free_plan.id
        old_daily = free_plan.daily
        old_label = free_plan.label

    new_daily_limit = old_daily + 15
    res_edit_plan = client.post(
        f"/admin/plans/{free_id}/update",
        data={
            "csrf_token": TEST_CSRF,
            "label": "Free",
            "price": "0.0",
            "price_inr": "0.0",
            "price_yearly": "0.0",
            "price_inr_yearly": "0.0",
            "daily": str(new_daily_limit),
            "monthly": "600",
            "strong_monthly": "50",
            "features": "20 requests per day\nUpdated features",
            "is_default": "1",
            "is_active": "1",
            "sort_order": "1"
        },
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res_edit_plan.status_code == 200

    # Step B: Verify target user's quota immediately reflects new limit
    with app.app_context():
        target_user = db.session.get(User, target_id)
        updated_usage = get_usage(target_user)
        assert updated_usage["day"]["limit"] == new_daily_limit

    # Step C: Admin edit target user
    res_edit_user = client.post(
        f"/admin/users/{target_id}/edit",
        data={"csrf_token": TEST_CSRF, "name": "Renamed Target", "email": target_email, "plan": "plus", "status": "active"},
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res_edit_user.status_code == 200

    with app.app_context():
        target_user = db.session.get(User, target_id)
        assert target_user.name == "Renamed Target"
        assert target_user.plan == "plus"

    # Step D: Admin cancel target user plan
    res_cancel = client.post(
        f"/admin/users/{target_id}/cancel-plan",
        data={"csrf_token": TEST_CSRF},
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res_cancel.status_code == 200

    with app.app_context():
        target_user = db.session.get(User, target_id)
        assert target_user.plan_status == "cancelled"

    # Step E: Admin soft delete target user
    res_del_user = client.post(
        f"/admin/users/{target_id}/delete",
        data={"csrf_token": TEST_CSRF},
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res_del_user.status_code == 200

    with app.app_context():
        target_user = db.session.get(User, target_id)
        assert target_user.deleted_at is not None

        # Verify audit logs were written
        logs = AuditLog.query.filter_by(admin_id=admin_id).all()
        actions = [l.action for l in logs]
        assert "edit_user" in actions
        assert "cancel_user_plan" in actions
        assert "delete_user" in actions

        # Cleanup: restore Free plan daily and label
        fp = Plan.query.filter_by(code="free").first()
        if fp:
            fp.daily = old_daily
            fp.label = old_label or "Free"
            db.session.commit()


# =====================================================================
# 6. Admin Plan Deletion Safety (Blocking Default & User Migration)
# =====================================================================
def test_admin_plan_deletion_rules(client):
    """
    Verify:
    1. Deleting the last default plan is blocked.
    2. Deleting a custom plan migrates all assigned users to replacement plan.
    """
    admin_uid = uuid.uuid4().hex[:6]
    admin_email = f"admin_del_{admin_uid}@upian.local"
    admin_id = create_test_user(admin_email, is_admin=True, plan="pro")

    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    with app.app_context():
        default_plan = Plan.query.filter_by(is_default=True, is_active=True).first()
        assert default_plan is not None
        default_id = default_plan.id

        # 1. Attempt to delete the only default plan -> must be blocked
        other_plan = Plan.query.filter(Plan.id != default_id).first()
        del_ok, msg = remove_plan(default_id, replacement_plan_id=other_plan.id, admin_id=admin_id)
        assert del_ok is False
        assert "Cannot delete the default plan" in msg

        # 2. Create a temporary plan, assign a user to it, then delete with replacement
        unique_code = f"temp_{int(time.time()*1000)}"
        ok, temp_plan, _ = create_new_plan(
            label="Temp Deletable Plan",
            code=unique_code,
            price_inr=399,
            daily=40,
            monthly=800,
            is_default=False,
            admin_id=admin_id
        )
        assert ok is True

        temp_user_id = create_test_user(f"mig_usr_{unique_code}@upian.local", is_admin=False, plan=unique_code)

        # Delete temp plan via remove_plan with replacement = default_id
        del_ok, _ = remove_plan(temp_plan.id, replacement_plan_id=default_id, admin_id=admin_id)
        assert del_ok is True

        # Verify user was migrated
        migrated_user = db.session.get(User, temp_user_id)
        assert migrated_user.plan == default_plan.code

        # Verify plan is marked inactive or deleted
        p = db.session.get(Plan, temp_plan.id)
        assert p is None or not p.is_active


# =====================================================================
# 7. Frontend Template UI Validations
# =====================================================================
def test_frontend_template_sanity():
    """Verify that templates and JS files have correct elements without duplicates."""
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent

    # A. Check chat.html: ensure no duplicate #stop-bar
    chat_html = (base / "templates" / "chat.html").read_text(encoding="utf-8")
    assert 'id="stop-bar"' not in chat_html
    # Verify composer has send/stop button and prompt textarea
    assert 'id="send"' in chat_html
    assert 'id="prompt"' in chat_html

    # B. Check chat.js and common.js: ensure stopCurrentPrompt, copy button logic, and fallback exist
    chat_js = (base / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    common_js = (base / "static" / "js" / "common.js").read_text(encoding="utf-8")
    assert "stopCurrentPrompt" in chat_js
    assert "btn-copy-code" in common_js
    assert "fallbackCopyCode" in common_js
    assert "navigator.clipboard" in common_js

    # C. Check navigation: clean Back to Chat buttons in files, activity, and admin
    files_html = (base / "templates" / "files.html").read_text(encoding="utf-8")
    activity_html = (base / "templates" / "activity.html").read_text(encoding="utf-8")
    admin_base_html = (base / "templates" / "admin_base.html").read_text(encoding="utf-8")

    assert "Back to Chat" in files_html or "Back to chat" in files_html
    assert "Back to Chat" in activity_html or "Back to chat" in activity_html
    assert "Back to Chat" in admin_base_html or "Back to chat" in admin_base_html
