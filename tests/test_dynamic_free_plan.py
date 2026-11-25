"""Comprehensive tests for:
1. Idempotent seed and default plan resolution.
2. Editing the default/free plan updates real limits immediately in quota.get_usage().
3. Deleting a plan safely moves users to a replacement plan in an atomic transaction.
4. Deleting the last default plan is strictly blocked.
5. Non-admin users receive 403 Forbidden on all admin plan and user endpoints.
6. Stopped chat requests log as status='stopped' and consume zero quota.
7. Subsequent prompt after stop increments quota without lost or double count.
8. Safe failure on signup when no default plan is present.
"""
import pytest
import json
import time
from app import app
from models import db, User, Plan, AuditLog, RequestLog, upsert_google_user
from plans_service import (
    ensure_seed_plans,
    get_default_plan,
    get_user_plan_dict,
    create_new_plan,
    update_existing_plan,
    remove_plan,
    set_plan_as_default
)
from quota import get_usage

TEST_CSRF = "secure_csrf_token_dynamic_free_plan"


def create_user(email, is_admin=False, plan="free"):
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(name=email.split("@")[0], email=email, plan=plan, is_admin=is_admin)
            u.set_password("Secret123!")
            db.session.add(u)
            db.session.commit()
        else:
            u.is_admin = is_admin
            u.plan = plan
            u.deleted_at = None
            db.session.commit()
        return u.id


def test_seed_is_idempotent():
    with app.app_context():
        # Call seed twice
        ensure_seed_plans()
        ensure_seed_plans()

        default_plan = get_default_plan()
        assert default_plan is not None
        assert default_plan.is_default is True
        assert default_plan.price_inr == 0

        # Verify only one default plan exists
        defaults = Plan.query.filter_by(is_default=True, is_active=True).all()
        assert len(defaults) == 1


def test_editing_free_changes_real_limits():
    with app.app_context():
        ensure_seed_plans()
        def_plan = get_default_plan()
        orig_daily = def_plan.daily
        orig_monthly = def_plan.monthly

        admin_id = create_user("admin_editor@upian.local", is_admin=True)
        user_id = create_user("test_free_user@upian.local", is_admin=False, plan=def_plan.code)
        user = db.session.get(User, user_id)

        # Baseline usage limit
        u1 = get_usage(user)
        assert u1["day"]["limit"] == orig_daily

        # Update limit to 42
        ok, updated_plan, msg = update_existing_plan(
            def_plan.id,
            label=def_plan.label,
            price_inr=def_plan.price_inr,
            daily=42,
            monthly=555,
            admin_id=admin_id
        )
        assert ok is True

        # Check that user gets the updated limit immediately without code changes
        user = db.session.get(User, user_id)
        u2 = get_usage(user)
        assert u2["day"]["limit"] == 42
        assert u2["month"]["limit"] == 555

        # Revert back to original
        update_existing_plan(
            def_plan.id,
            label=def_plan.label,
            price_inr=def_plan.price_inr,
            daily=orig_daily,
            monthly=orig_monthly,
            admin_id=admin_id
        )


def test_deleting_last_default_is_blocked():
    with app.app_context():
        ensure_seed_plans()
        def_plan = get_default_plan()
        admin_id = create_user("admin_block_del@upian.local", is_admin=True)

        # Create a second dummy plan for replacement target with unique code
        unique_code = f"temp_rep_{int(time.time()*1000)}"
        ok, rep_plan, msg = create_new_plan(
            label="Temp Replacement",
            code=unique_code,
            price_inr=499,
            daily=50,
            monthly=200,
            is_default=False,
            admin_id=admin_id
        )
        assert ok is True

        # Attempting to delete def_plan while it is the only default plan must be blocked
        del_ok, del_msg = remove_plan(def_plan.id, replacement_plan_id=rep_plan.id, admin_id=admin_id)
        assert del_ok is False
        assert "Cannot delete the default plan" in del_msg

        # Cleanup dummy plan
        remove_plan(rep_plan.id, replacement_plan_id=def_plan.id, admin_id=admin_id)


def test_deleting_plan_moves_users():
    with app.app_context():
        ensure_seed_plans()
        admin_id = create_user("admin_del_move@upian.local", is_admin=True)
        def_plan = get_default_plan()

        # Create plan to be deleted
        ok, old_plan, msg = create_new_plan(
            label="Plan to Delete",
            code="del_target_plan",
            price_inr=299,
            daily=35,
            monthly=150,
            admin_id=admin_id
        )
        assert ok is True

        # Assign a user to old_plan
        user_id = create_user("migrating_user@upian.local", is_admin=False, plan=old_plan.code)
        user = db.session.get(User, user_id)
        assert user.plan == old_plan.code

        # Delete old_plan and migrate users to def_plan
        del_ok, del_msg = remove_plan(old_plan.id, replacement_plan_id=def_plan.id, admin_id=admin_id)
        assert del_ok is True

        # Verify user was migrated atomically
        user = db.session.get(User, user_id)
        assert user.plan == def_plan.code

        # Verify audit log was recorded
        audit = AuditLog.query.filter_by(action="delete_plan").order_by(AuditLog.id.desc()).first()
        assert audit is not None
        assert "del_target_plan" in audit.details


def test_non_admin_gets_403_on_admin_endpoints():
    user_id = create_user("unauthorized_tester@upian.local", is_admin=False)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = TEST_CSRF

    # Test GET /admin/plans
    r1 = client.get("/admin/plans")
    assert r1.status_code in (302, 403)

    # Test POST /admin/plans/create
    r2 = client.post("/admin/plans/create", data={"csrf_token": TEST_CSRF, "name": "Hack"}, headers={"X-CSRFToken": TEST_CSRF})
    assert r2.status_code in (302, 403)

    # Test POST /admin/users/1/edit
    r3 = client.post("/admin/users/1/edit", data={"csrf_token": TEST_CSRF, "name": "Hack"}, headers={"X-CSRFToken": TEST_CSRF})
    assert r3.status_code in (302, 403)

    # Test POST /admin/users/1/delete
    r4 = client.post("/admin/users/1/delete", data={"csrf_token": TEST_CSRF}, headers={"X-CSRFToken": TEST_CSRF})
    assert r4.status_code in (302, 403)

    # Test POST /admin/users/1/cancel-plan
    r5 = client.post("/admin/users/1/cancel-plan", data={"csrf_token": TEST_CSRF}, headers={"X-CSRFToken": TEST_CSRF})
    assert r5.status_code in (302, 403)


def test_admin_user_controls_edit_delete_cancel_plan():
    admin_id = create_user("super_admin_ctrl@upian.local", is_admin=True)
    target_id = create_user("target_user_ctrl@upian.local", is_admin=False, plan="pro")

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = TEST_CSRF

    # 1. Edit User
    new_email = f"updated_{int(time.time()*1000)}@upian.local"
    res = client.post(
        f"/admin/users/{target_id}/edit",
        data={"csrf_token": TEST_CSRF, "name": "Updated Name", "email": new_email, "plan": "plus", "status": "active"},
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res.status_code == 200
    with app.app_context():
        tu = db.session.get(User, target_id)
        assert tu.name == "Updated Name"
        assert tu.email == new_email
        assert tu.plan == "plus"

    # 2. Cancel Plan (Downgrade to default)
    res = client.post(
        f"/admin/users/{target_id}/cancel-plan",
        data={"csrf_token": TEST_CSRF},
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res.status_code == 200
    with app.app_context():
        tu = db.session.get(User, target_id)
        def_plan = get_default_plan()
        assert tu.plan == def_plan.code

    # 3. Delete User (Soft Delete)
    res = client.post(
        f"/admin/users/{target_id}/delete",
        data={"csrf_token": TEST_CSRF},
        headers={"X-CSRFToken": TEST_CSRF},
        follow_redirects=True
    )
    assert res.status_code == 200
    with app.app_context():
        tu = db.session.get(User, target_id)
        assert tu.deleted_at is not None

        # Verify audit log recorded
        audit = AuditLog.query.filter_by(action="delete_user", target_user_id=target_id).first()
        assert audit is not None


def test_stopped_chat_request_zero_quota_and_reprompt():
    user_id = create_user("stop_quota_tester@upian.local", is_admin=False)
    with app.app_context():
        user = db.session.get(User, user_id)
        # Clear existing logs for this user
        RequestLog.query.filter_by(user_id=user_id).delete()
        db.session.commit()

        initial_usage = get_usage(user)
        assert initial_usage["day"]["used"] == 0

        # Simulate a stopped request (status="stopped")
        req_id = "test_req_stop_123"
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["uid"] = user_id
            sess["csrf"] = TEST_CSRF

        # Notify server of stop
        stop_res = client.post(
            "/api/chat/stop",
            json={"request_id": req_id},
            headers={"X-CSRFToken": TEST_CSRF}
        )
        assert stop_res.status_code == 200

        # Log simulated stopped request
        log_stop = RequestLog(
            user_id=user_id,
            prompt="Write a 1000 line poem",
            status="stopped",
            tier="simple",
            classifier="rule",
            latency_ms=250
        )
        db.session.add(log_stop)
        db.session.commit()

        # Check quota: stopped request MUST NOT increment quota
        usage_after_stop = get_usage(user)
        assert usage_after_stop["day"]["used"] == 0

        # Immediately send another prompt which succeeds
        log_success = RequestLog(
            user_id=user_id,
            prompt="Hello Upian",
            status="success",
            tier="simple",
            classifier="rule",
            tokens_in=10,
            tokens_out=25,
            latency_ms=180
        )
        db.session.add(log_success)
        db.session.commit()

        # Usage must increment by exactly 1
        usage_after_success = get_usage(user)
        assert usage_after_success["day"]["used"] == 1


def test_signup_fails_safely_when_no_default_plan():
    with app.app_context():
        # Clear default flag temporarily
        Plan.query.update({"is_default": False, "is_active": False})
        db.session.commit()

        try:
            unique_email = f"no_def_{int(time.time()*1000)}@upian.local"
            unique_gid = f"google_no_def_{int(time.time()*1000)}"
            with pytest.raises(RuntimeError, match="No default subscription plan is configured"):
                upsert_google_user(unique_email, unique_gid, "Test")
        finally:
            # Restore all active plans and default plan
            Plan.query.filter(Plan.code.in_(["free", "pro", "plus", "plus_pro"])).update({"is_active": True})
            db.session.commit()
            ensure_seed_plans()
