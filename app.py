import os
import certifi

# Fix broken system TLS CA bundles (e.g. invalid PostgreSQL CURL_CA_BUNDLE pointing to missing file)
for _ca_var in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
    _val = os.environ.get(_ca_var)
    if _val and not os.path.exists(_val):
        os.environ.pop(_ca_var, None)

_valid_cert = certifi.where()
if not os.environ.get("REQUESTS_CA_BUNDLE") or not os.path.exists(os.environ["REQUESTS_CA_BUNDLE"]):
    os.environ["REQUESTS_CA_BUNDLE"] = _valid_cert
if not os.environ.get("SSL_CERT_FILE") or not os.path.exists(os.environ["SSL_CERT_FILE"]):
    os.environ["SSL_CERT_FILE"] = _valid_cert

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import uuid
import urllib.parse
import requests
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session,
                   stream_with_context, url_for)
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException

import llm
import special
import image_gen
import vision
import multimodal
from config import PLAN_ORDER, PLANS, Config, env, validate_config
import filetools
from file_routes import read_docs_text, register_files, save_images_pdf, save_export
from models import (AuditLog, RequestLog, User, Payment, WebhookEvent, db, iso, utcnow,
                    upsert_google_user, update_user_plan_idempotent, verify_webhook_signature)
from quota import get_usage, local_tz, windows
from router import classifier
import plans_service

logger = logging.getLogger("upian.app")

ACTIVE_CHAT_REQUESTS = {}
ACTIVE_CHAT_LOCK = threading.Lock()

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)


# ----------------------------------------------------------------------------- setup
def seed_admin():
    email = (Config.ADMIN_EMAIL or "").lower()
    if not email or not Config.ADMIN_PASSWORD:
        return
    try:
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=Config.ADMIN_NAME or "Admin", email=email, plan="plus_pro", is_admin=True)
            user.set_password(Config.ADMIN_PASSWORD)
            db.session.add(user)
            db.session.commit()
        elif not user.is_admin:
            user.is_admin = True
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("Could not seed admin user: %s", exc)


def init_database_safe(app_instance, max_retries=3, retry_delay=0.5):
    """
    Safely initialize the database with connection verification, retries, and fallback.
    Prevents unhandled crashes on startup if PostgreSQL or socket access is unavailable.
    """
    app_instance.config["DB_AVAILABLE"] = False
    app_instance.config["DB_STATUS"] = "initializing"
    app_instance.config["DB_ERROR"] = None

    for attempt in range(1, max_retries + 1):
        try:
            with app_instance.app_context():
                # Verify connection with timeout
                db.session.execute(db.text("SELECT 1"))
                db.create_all()
                try:
                    from migrations.migrate_v1 import run_migration
                    run_migration(app=app_instance)
                except Exception as m_exc:
                    logger.warning("Auto-migration check notice: %s", m_exc)
                seed_admin()
                try:
                    import plans_service
                    plans_service.init_default_plans(app_instance)
                except Exception as p_exc:
                    logger.warning("Default plans initialization notice: %s", p_exc)
                app_instance.config["DB_AVAILABLE"] = True
                app_instance.config["DB_STATUS"] = "connected"
                logger.info("Database connection established successfully on attempt %d.", attempt)
                return True
        except Exception as exc:
            logger.warning("Database connection attempt %d/%d failed: %s", attempt, max_retries, exc)
            app_instance.config["DB_ERROR"] = str(exc)
            if attempt < max_retries:
                time.sleep(retry_delay)

    # If primary database (PostgreSQL) is unreachable:
    logger.error("Primary database is unreachable. Attempting SQLite fallback.")
    if getattr(Config, "ALLOW_SQLITE_FALLBACK", True):
        try:
            sqlite_uri = "sqlite:///" + os.path.join(Config.BASE_DIR, "upian.db")
            logger.warning("Falling back to local SQLite database: %s", sqlite_uri)
            app_instance.config["SQLALCHEMY_DATABASE_URI"] = sqlite_uri
            with app_instance.app_context():
                db.engine.dispose()
                db.init_app(app_instance)
                db.create_all()
                try:
                    from migrations.migrate_v1 import run_migration
                    run_migration(app=app_instance)
                except Exception as m_exc:
                    logger.warning("Auto-migration check notice: %s", m_exc)
                seed_admin()
                try:
                    import plans_service
                    plans_service.init_default_plans(app_instance)
                except Exception as p_exc:
                    logger.warning("Default plans initialization notice: %s", p_exc)
                app_instance.config["DB_AVAILABLE"] = True
                app_instance.config["DB_STATUS"] = "connected_sqlite_fallback"
                logger.info("SQLite fallback database initialized successfully.")
                return True
        except Exception as fallback_exc:
            logger.critical("SQLite fallback failed: %s", fallback_exc)
            app_instance.config["DB_ERROR"] = str(fallback_exc)

    app_instance.config["DB_AVAILABLE"] = False
    app_instance.config["DB_STATUS"] = "disconnected"
    logger.warning("Application running in degraded mode without database.")
    return False


# Validate config & initialize database safely
validate_config()
init_database_safe(app)

if Config.PRELOAD_CLASSIFIER:
    threading.Thread(target=classifier.load, daemon=True).start()


# ----------------------------------------------------------------------------- helpers
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.user:
            if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
                return jsonify(error="Your session ended. Sign in again."), 401
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.user:
            if request.path.startswith("/admin/api/"):
                return jsonify(error="Your session ended. Sign in again."), 401
            return redirect(url_for("login", next=request.path))
        if not g.user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    if request.endpoint == "static":
        return
    uid = session.get("uid")
    if uid:
        try:
            user = db.session.get(User, uid)
            if user and user.active and not user.deleted_at:
                g.user = user
            else:
                session.pop("uid", None)
        except Exception as exc:
            logger.warning("Database error loading user session: %s", exc)
            g.user = None

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Webhooks and external auth callbacks use cryptographic signatures rather than CSRF cookies
        if request.path in ("/api/payment/webhook", "/auth/google/callback"):
            return
        sent = (
            request.headers.get("X-CSRF-Token")
            or request.headers.get("X-CSRFToken")
            or request.form.get("csrf_token")
            or ""
        )
        expected = session.get("csrf") or ""
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, description="Security token missing or expired. Reload the page and try again.")


@app.context_processor
def inject_globals():
    # Build unique DISPLAY_PLANS from database active plans
    display_plans = []
    seen = set()
    try:
        from models import Plan
        active_db_plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order.asc(), Plan.id.asc()).all()
        if active_db_plans:
            for p in active_db_plans:
                if p.code == "custom" or p.code in seen:
                    continue
                seen.add(p.code)
                p_dict = p.to_plan_dict()
                display_plans.append(p_dict)
                PLANS[p.code] = p_dict
                if p.code == "plus" or (p.label and p.label.strip().lower() == "pro"):
                    PLANS["pro"] = p_dict
                    PLANS["plus"] = p_dict
                elif p.code == "plus_pro" or (p.label and p.label.strip().lower() in ("pro plus", "pro_plus")):
                    PLANS["pro_plus"] = p_dict
                    PLANS["plus_pro"] = p_dict
        else:
            for code in PLAN_ORDER:
                if code in seen or code == "custom":
                    continue
                p_info = PLANS.get(code)
                if not p_info:
                    continue
                canonical_id = p_info.get("id") or code
                if canonical_id in seen or canonical_id == "custom":
                    continue
                seen.add(canonical_id)
                display_plans.append(p_info)
    except Exception:
        for code in PLAN_ORDER:
            if code in seen or code == "custom":
                continue
            p_info = PLANS.get(code)
            if not p_info:
                continue
            canonical_id = p_info.get("id") or code
            if canonical_id in seen or canonical_id == "custom":
                continue
            seen.add(canonical_id)
            display_plans.append(p_info)

    def_p = plans_service.get_default_plan()
    def_code = def_p.code if def_p else "free"

    return dict(
        csrf_token=csrf_token,
        me=g.get("user"),
        PLANS=PLANS,
        PLAN_ORDER=PLAN_ORDER,
        DISPLAY_PLANS=display_plans,
        DEFAULT_PLAN=def_code,
        tz_label=Config.APP_TIMEZONE,
        google_client_id=Config.GOOGLE_CLIENT_ID,
    )


@app.template_filter("localdt")
def localdt(dt, fmt="%d %b, %H:%M"):
    if not dt:
        return "-"
    return dt.replace(tzinfo=timezone.utc).astimezone(local_tz()).strftime(fmt)


@app.template_filter("num")
def num(value):
    return "{:,}".format(int(value or 0))


@app.errorhandler(HTTPException)
def handle_http(err):
    if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
        return jsonify(error=err.description), err.code
    return render_template("error.html", code=err.code, message=err.description), err.code


@app.errorhandler(500)
def handle_500(err):
    if request.path.startswith("/api/"):
        return jsonify(error="Something went wrong on our side."), 500
    return render_template("error.html", code=500, message="Something went wrong on our side."), 500


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ----------------------------------------------------------------------------- auth
_failed_logins = {}


def _throttled(ip):
    now = time.time()
    recent = [t for t in _failed_logins.get(ip, []) if now - t < 600]
    _failed_logins[ip] = recent
    return len(recent) >= 10


def _sign_in(user):
    session.clear()
    session["uid"] = user.id
    session.permanent = True
    user.last_login = utcnow()
    db.session.commit()


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if _throttled(ip):
            error = "Too many failed attempts. Try again in a few minutes."
        else:
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                if user.deleted_at or not user.active:
                    error = "This account is disabled. Contact support."
                else:
                    _sign_in(user)
                    return redirect(safe_next(request.args.get("next")) or url_for("home"))
            else:
                _failed_logins.setdefault(ip, []).append(time.time())
                error = "Email or password is wrong."
    return render_template("auth.html", mode="login", error=error)


def sync_upian_table(user):
    """Keep custom PostgreSQL 'upian' table in sync if it exists."""
    try:
        from sqlalchemy import text
        label_map = {"free": "Free", "plus": "Pro", "pro": "Pro", "plus_pro": "Pro Plus"}
        if getattr(user, "subscription_id", None) and str(user.subscription_id).startswith("custom:"):
            plan_label = str(user.subscription_id).split("custom:", 1)[1].strip()
        else:
            plan_label = label_map.get(user.plan, user.plan)
        db.session.execute(text("""
            INSERT INTO upian (name, email, plan)
            VALUES (:name, :email, :plan)
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, plan = EXCLUDED.plan
        """), {"name": user.name, "email": user.email, "plan": plan_label})
        db.session.commit()
    except Exception:
        db.session.rollback()


@app.post("/api/user/nickname")
@login_required
def api_update_nickname():
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or request.form.get("name") or "").strip()
    if not new_name:
        return jsonify(error="Nickname cannot be empty.", code="invalid_name"), 400
    if len(new_name) > 80:
        new_name = new_name[:80]
    
    g.user.name = new_name
    db.session.commit()
    sync_upian_table(g.user)
    return jsonify(success=True, name=new_name, message="Nickname updated successfully!")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:80]
        email = (request.form.get("email") or "").strip().lower()[:160]
        password = request.form.get("password") or ""
        if not name:
            error = "Enter your name."
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Use a password with at least 8 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with this email already exists."
        else:
            from plans_service import get_default_plan
            def_plan = get_default_plan()
            if not def_plan:
                logger.error("Signup failed: No default subscription plan configured in database.")
                return render_template("auth.html", mode="register", error="Registration temporarily unavailable: No default plan is configured. Please contact an administrator.")
            user = User(name=name, email=email, plan=def_plan.code)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            sync_upian_table(user)
            _sign_in(user)
            return redirect(url_for("home"))
    return render_template("auth.html", mode="register", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------- user pages
@app.get("/")
@login_required
def home():
    def_plan = plans_service.get_default_plan()
    boot = {
        "uid": g.user.id,
        "plan": g.user.plan,
        "default_plan": def_plan.code if def_plan else "free",
        "can_image_gen": plans_service.get_user_plan_dict(g.user).get("image_gen", False),
        "usage": get_usage(g.user),
        "threshold": Config.CLASSIFIER_THRESHOLD,
    }
    return render_template("chat.html", boot=boot, max_chars=Config.MAX_PROMPT_CHARS)


@app.get("/activity")
@login_required
def activity():
    plans_list = []
    for p in PLAN_ORDER:
        p_info = PLANS[p]
        is_cur = (g.user.plan == p or (p == "plus" and g.user.plan == "pro") or (p == "plus_pro" and g.user.plan == "pro_plus"))
        plans_list.append({
            "id": p,
            "label": p_info["label"],
            "price": p_info.get("price", 0),
            "price_str": p_info.get("price_str", "$0"),
            "period": p_info.get("period", "/mo"),
            "description": p_info.get("description", ""),
            "daily": p_info["daily"],
            "monthly": p_info["monthly"],
            "strong_monthly": p_info["strong_monthly"],
            "image_gen": p_info.get("image_gen", False),
            "image_daily": p_info.get("image_daily", 0),
            "features": p_info.get("features", []),
            "current": is_cur,
        })
    boot = {
        "threshold": Config.CLASSIFIER_THRESHOLD,
        "usage": get_usage(g.user),
        "plans": plans_list,
        "current_plan": g.user.plan,
        "csrf_token": csrf_token(),
    }
    return render_template("activity.html", boot=boot, plans=plans_list, current_plan=g.user.plan)


@app.get("/plans")
@login_required
def plans_page():
    plans_list = []
    seen = set()
    try:
        from models import Plan
        import plans_service
        plans_service.sync_plans_to_memory()
        active_db_plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order.asc(), Plan.id.asc()).all()
        if active_db_plans:
            for p in active_db_plans:
                if p.code == "custom" or p.code in seen:
                    continue
                seen.add(p.code)
                p_dict = p.to_plan_dict()
                canonical_id = p.code
                is_cur = (g.user.plan == p.code or (p.code == "plus" and g.user.plan == "pro") or (p.code == "plus_pro" and g.user.plan == "pro_plus") or (g.user.plan == canonical_id))
                p_dict["current"] = is_cur
                plans_list.append(p_dict)
    except Exception:
        pass

    if not plans_list:
        for p in PLAN_ORDER:
            if p == "custom" or p in seen:
                continue
            p_info = PLANS.get(p)
            if not p_info:
                continue
            canonical_id = p_info.get("id") or p
            if canonical_id in seen:
                continue
            seen.add(canonical_id)
            is_cur = (g.user.plan == p or (p == "plus" and g.user.plan == "pro") or (p == "plus_pro" and g.user.plan == "pro_plus") or (g.user.plan == canonical_id))
            p_dict = dict(p_info)
            p_dict["current"] = is_cur
            plans_list.append(p_dict)

    boot = {
        "uid": g.user.id,
        "plan": g.user.plan,
        "plans": plans_list,
        "csrf_token": csrf_token(),
        "usage": get_usage(g.user),
    }
    return render_template("plans.html", plans=plans_list, current_plan=g.user.plan, boot=boot)


@app.get("/api/payment/plans")
@login_required
def api_payment_plans():
    plans_list = []
    seen = set()
    try:
        from models import Plan
        import plans_service
        plans_service.sync_plans_to_memory()
        active_db_plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order.asc(), Plan.id.asc()).all()
        if active_db_plans:
            for p in active_db_plans:
                if p.code == "custom" or p.code in seen:
                    continue
                seen.add(p.code)
                p_dict = p.to_plan_dict()
                is_cur = (g.user.plan == p.code or (p.code == "plus" and g.user.plan == "pro") or (p.code == "plus_pro" and g.user.plan == "pro_plus"))
                p_dict["current"] = is_cur
                plans_list.append(p_dict)
    except Exception:
        pass

    if not plans_list:
        for p in PLAN_ORDER:
            if p == "custom" or p in seen:
                continue
            seen.add(p)
            p_info = PLANS.get(p)
            if not p_info:
                continue
            is_cur = (g.user.plan == p or (p == "plus" and g.user.plan == "pro") or (p == "plus_pro" and g.user.plan == "pro_plus"))
            p_dict = dict(p_info)
            p_dict["current"] = is_cur
            plans_list.append(p_dict)

    razorpay_enabled = bool(Config.RAZORPAY_KEY_ID and Config.RAZORPAY_KEY_SECRET)
    return jsonify(
        plans=plans_list,
        current_plan=g.user.plan,
        usage=get_usage(g.user),
        gateway={
            "name": "razorpay" if razorpay_enabled else "simulated",
            "razorpay_enabled": razorpay_enabled,
            "razorpay_key_id": Config.RAZORPAY_KEY_ID,
            "currency": getattr(Config, "PAYMENT_CURRENCY", "USD"),
        }
    )


@app.post("/api/payment/razorpay/order")
@login_required
def api_payment_razorpay_order():
    data = request.get_json(silent=True) or {}
    raw_plan = (data.get("plan") or "").strip().lower()
    custom_plan_name = (data.get("custom_plan_name") or data.get("plan_name") or "").strip()
    custom_amount = data.get("amount")
    billing_cycle = (data.get("billing_cycle") or "monthly").strip().lower()

    currency = getattr(Config, "PAYMENT_CURRENCY", "INR").upper()

    if raw_plan in ("custom", "modular"):
        amount = float(custom_amount or (1999 if billing_cycle == "yearly" else 499))
        plan_id = "plus_pro" if amount >= 1500 else "plus"
        plan_label = custom_plan_name or "Custom Plan"
    else:
        # Dynamically lookup authoritative plan from database first
        from models import Plan
        import plans_service
        plans_service.sync_plans_to_memory()

        db_plan = None
        if raw_plan in ("pro", "plus"):
            db_plan = Plan.query.filter(Plan.is_active == True, Plan.code.in_(["plus", "pro"])).first()
        elif raw_plan in ("pro_plus", "plus_pro"):
            db_plan = Plan.query.filter(Plan.is_active == True, Plan.code.in_(["plus_pro", "pro_plus"])).first()
        else:
            db_plan = Plan.query.filter(Plan.is_active == True, Plan.code == raw_plan).first()

        if db_plan:
            plan_id = db_plan.code
            plan_label = custom_plan_name or db_plan.label
            if billing_cycle == "yearly":
                if currency == "INR":
                    amount = float(db_plan.price_inr_yearly if db_plan.price_inr_yearly and db_plan.price_inr_yearly > 0 else (db_plan.price_inr * 10))
                else:
                    amount = float(db_plan.price_yearly if db_plan.price_yearly and db_plan.price_yearly > 0 else (db_plan.price * 10))
            else:
                amount = float(db_plan.price_inr if currency == "INR" else db_plan.price)
        else:
            target_key = "plus" if raw_plan == "pro" else ("plus_pro" if raw_plan == "pro_plus" else raw_plan)
            p_chk = PLANS.get(target_key) or PLANS.get(raw_plan)
            if not p_chk or p_chk.get("price", 0) <= 0:
                return jsonify(error="Invalid subscription plan selected.", code="invalid_plan"), 400
            plan_id = target_key
            plan_label = custom_plan_name or p_chk.get("label", raw_plan.capitalize())
            if billing_cycle == "yearly":
                amount = float(p_chk.get("price_inr_yearly", 9999) if currency == "INR" else p_chk.get("price_yearly", 120.0))
            else:
                amount = float(p_chk.get("price_inr", 999) if currency == "INR" else p_chk.get("price", 15.0))

    receipt = f"rcpt_{g.user.id}_{uuid.uuid4().hex[:8]}"
    razorpay_key = Config.RAZORPAY_KEY_ID
    razorpay_secret = Config.RAZORPAY_KEY_SECRET

    if razorpay_key and razorpay_secret:
        try:
            import requests
            order_data = {
                "amount": int(amount * 100),
                "currency": currency,
                "receipt": receipt,
                "notes": {"user_id": g.user.id, "plan": plan_id, "billing_cycle": billing_cycle, "custom_plan_name": plan_label, "email": g.user.email},
            }
            res = requests.post(
                "https://api.razorpay.com/v1/orders",
                auth=(razorpay_key, razorpay_secret),
                json=order_data,
                timeout=10,
            )
            if res.status_code in (200, 201):
                ord_json = res.json()
                return jsonify(
                    success=True,
                    order_id=ord_json["id"],
                    key_id=razorpay_key,
                    amount=amount,
                    currency=currency,
                    plan=plan_id,
                    plan_label=plan_label,
                    billing_cycle=billing_cycle,
                    razorpay_enabled=True,
                )
            else:
                app.logger.warning(f"Razorpay order API returned {res.status_code}: {res.text}")
        except Exception as e:
            app.logger.warning(f"Razorpay order creation fallback to simulated: {e}")

    # Fallback to simulated/test order
    mock_order_id = "order_sim_" + uuid.uuid4().hex[:14]
    return jsonify(
        success=True,
        order_id=mock_order_id,
        key_id=razorpay_key or "rzp_test_simulated",
        amount=amount,
        currency=currency,
        plan=plan_id,
        plan_label=plan_label,
        billing_cycle=billing_cycle,
        razorpay_enabled=bool(razorpay_key),
        simulated=True,
    )


@app.post("/api/payment/process")
@login_required
def api_payment_process():
    data = request.get_json(silent=True) or {}
    raw_plan = (data.get("plan") or "").strip().lower()
    custom_plan_name = (data.get("custom_plan_name") or data.get("plan_name") or "").strip()
    custom_features = data.get("custom_features") or []
    custom_amount = data.get("amount")
    billing_cycle = (data.get("billing_cycle") or "monthly").strip().lower()
    duration_days = 365 if billing_cycle == "yearly" else 30

    if raw_plan in ("custom", "modular") or bool(custom_features) or bool(custom_plan_name):
        is_custom = True
        plan_id = "plus_pro" if ("unlimited" in custom_features or "high_quota" in custom_features or (custom_amount and float(custom_amount) >= 1500)) else "plus"
    elif raw_plan == "pro":
        is_custom = False
        plan_id = "plus"
    elif raw_plan == "pro_plus":
        is_custom = False
        plan_id = "plus_pro"
    else:
        is_custom = False
        plan_id = raw_plan

    if plan_id not in PLANS and raw_plan not in ("custom", "modular"):
        return jsonify(error="Invalid subscription plan selected.", code="invalid_plan"), 400

    plan_info = PLANS.get(plan_id, PLANS["plus"])
    if plan_id == "free":
        g.user.plan = "free"
        g.user.subscription_id = None
        db.session.commit()
        sync_upian_table(g.user)
        return jsonify(
            success=True,
            message="Your account has been switched to the Free plan.",
            plan="free",
            plan_label="Free",
            usage=get_usage(g.user),
        )

    # Check for Razorpay signature if keys are configured
    razorpay_payment_id = data.get("razorpay_payment_id")
    razorpay_order_id = data.get("razorpay_order_id")
    razorpay_signature = data.get("razorpay_signature")

    if Config.RAZORPAY_KEY_ID and Config.RAZORPAY_KEY_SECRET and razorpay_payment_id and razorpay_order_id and razorpay_signature:
        msg = f"{razorpay_order_id}|{razorpay_payment_id}"
        expected_sig = hmac.new(Config.RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, razorpay_signature):
            return jsonify(error="Invalid Razorpay payment signature.", code="signature_verification_failed"), 400
        tx_id = razorpay_payment_id
        payment_method = "razorpay"
    else:
        # Simulated secure checkout mode (instant test activation)
        payment_method = data.get("payment_method") or "card"
        tx_id = razorpay_payment_id or ("UPIAN-PAY-" + uuid.uuid4().hex[:12].upper())

    currency = getattr(Config, "PAYMENT_CURRENCY", "INR").upper()
    if custom_amount is not None:
        try:
            amount = float(custom_amount)
        except (ValueError, TypeError):
            amount = 0.0
    else:
        amount = 0.0

    if amount <= 0:
        from models import Plan
        db_plan = None
        if raw_plan in ("pro", "plus"):
            db_plan = Plan.query.filter(Plan.is_active == True, Plan.code.in_(["plus", "pro"])).first()
        elif raw_plan in ("pro_plus", "plus_pro"):
            db_plan = Plan.query.filter(Plan.is_active == True, Plan.code.in_(["plus_pro", "pro_plus"])).first()
        else:
            db_plan = Plan.query.filter(Plan.is_active == True, Plan.code == raw_plan).first()

        if db_plan:
            if billing_cycle == "yearly":
                amount = float(db_plan.price_inr_yearly if db_plan.price_inr_yearly and db_plan.price_inr_yearly > 0 else (db_plan.price_inr * 10))
            else:
                amount = float(db_plan.price_inr if currency == "INR" else db_plan.price)
        else:
            if billing_cycle == "yearly":
                amount = float(plan_info.get("price_inr_yearly", 9999) if currency == "INR" else plan_info.get("price_yearly", 120.0))
            else:
                amount = float(plan_info.get("price_inr", 999) if currency == "INR" else plan_info.get("price", 15.0))

    success, payment, msg = update_user_plan_idempotent(
        user_id=g.user.id,
        plan=plan_id,
        transaction_id=tx_id,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        duration_days=duration_days,
    )

    if custom_plan_name:
        g.user.subscription_id = f"custom:{custom_plan_name}"
        db.session.commit()

    sync_upian_table(g.user)

    curr_symbol = "₹" if currency == "INR" else "$"
    label = custom_plan_name if custom_plan_name else ("Custom Plan" if is_custom else plan_info["label"])
    return jsonify(
        success=True,
        transaction_id=tx_id,
        plan=plan_id,
        plan_label=label,
        billing_cycle=billing_cycle,
        duration_days=duration_days,
        amount=amount,
        currency=currency,
        is_custom=is_custom,
        custom_features=custom_features,
        message=f"Payment of {curr_symbol}{amount:.2f} successful! You are now upgraded to {label} ({billing_cycle.title()}).",
    )


@app.post("/api/payment/downgrade")
@login_required
def api_payment_downgrade():
    def_plan = plans_service.get_default_plan()
    def_code = def_plan.code if def_plan else "free"
    def_label = def_plan.label if def_plan else "Free"
    g.user.plan = def_code
    db.session.commit()
    sync_upian_table(g.user)
    return jsonify(
        success=True,
        message=f"Your account has been changed to the {def_label} plan.",
        plan=def_code,
        plan_label=def_label,
        usage=get_usage(g.user),
    )


register_files(app, login_required)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, classifier=classifier.status)


@app.get("/health")
def health():
    db_connected = False
    db_engine = "unknown"
    db_error = None
    try:
        db.session.execute(db.text("SELECT 1"))
        db_connected = True
        db_engine = db.engine.dialect.name
    except Exception as exc:
        db_error = str(exc)

    status = "healthy" if db_connected else "degraded"
    return jsonify({
        "status": status,
        "app": {
            "name": "UPIAN AI Gateway",
            "status": "running",
            "timezone": Config.APP_TIMEZONE,
        },
        "database": {
            "status": "connected" if db_connected else "disconnected",
            "engine": db_engine,
            "error": db_error,
        },
        "model_routing": {
            "classifier": classifier.status if hasattr(classifier, "status") else "rule_based",
            "has_text_keys": bool(env("GEMINI_API_KEY") or env("OPENAI_API_KEY") or env("ANTHROPIC_API_KEY")),
            "has_image_keys": bool(env("IMAGE_GEN_API_KEY") or env("STABILITY_API_KEY") or env("IMAGEN_API_KEY")),
        },
        "chat_history": {
            "backend": "in_memory",
            "max_chats_per_user": 20,
            "persistence": "ephemeral_lost_on_restart",
        },
        "timestamp": utcnow().isoformat() + "Z",
    }), 200


@app.route("/auth/google")
def auth_google_login():
    """
    Initiates Google OAuth 2.0 authorization code flow.
    Redirects user to Google OAuth consent screen.
    """
    if not Config.GOOGLE_CLIENT_ID:
        flash("Google sign-in is not configured.", "err")
        return redirect(url_for("login"))

    next_url = safe_next(request.args.get("next"))
    if next_url:
        session["google_auth_next"] = next_url

    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state

    redirect_uri = Config.GOOGLE_REDIRECT_URI or url_for("auth_google_callback", _external=True)

    params = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(auth_url)


@app.route("/auth/google/callback", methods=["GET", "POST"])
def auth_google_callback():
    """
    Handles Google OAuth authentication callback.
    Supports:
      1. GET callback from Google OAuth 2.0 authorization code flow
      2. POST callback from Google Identity Services (GIS) with JWT credential / id_token
      3. POST callback with direct email and google_id payload
    Upserts user by google_id or email, preventing duplicates under concurrent requests.
    """
    email = None
    google_id = None
    name = ""

    if request.method == "GET":
        error = request.args.get("error")
        if error:
            logger.warning("Google OAuth error response: %s", error)
            flash("Google sign-in was cancelled or encountered an error.", "err")
            return redirect(url_for("login"))

        code = request.args.get("code")
        if not code:
            flash("Invalid Google authorization response: code missing.", "err")
            return redirect(url_for("login"))

        state = request.args.get("state")
        saved_state = session.pop("google_oauth_state", None)
        if saved_state and state != saved_state:
            logger.warning("Google OAuth state mismatch: sent=%s, expected=%s", state, saved_state)
            flash("Security verification failed during Google sign-in. Please try again.", "err")
            return redirect(url_for("login"))

        redirect_uri = Config.GOOGLE_REDIRECT_URI or url_for("auth_google_callback", _external=True)

        token_url = "https://oauth2.googleapis.com/token"
        token_payload = {
            "code": code,
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }

        try:
            token_resp = requests.post(token_url, data=token_payload, timeout=10)
            if token_resp.status_code != 200:
                logger.error("Google token exchange failed (%s): %s", token_resp.status_code, token_resp.text)
                flash("Failed to authenticate with Google. Please check your credentials.", "err")
                return redirect(url_for("login"))

            token_data = token_resp.json()
            id_token_jwt = token_data.get("id_token")
            access_token = token_data.get("access_token")

            if id_token_jwt:
                try:
                    from google.oauth2 import id_token
                    from google.auth.transport import requests as google_requests
                    idinfo = id_token.verify_oauth2_token(
                        id_token_jwt,
                        google_requests.Request(),
                        Config.GOOGLE_CLIENT_ID
                    )
                    email = idinfo.get("email")
                    google_id = idinfo.get("sub")
                    name = idinfo.get("name") or idinfo.get("given_name") or ""
                except Exception as v_err:
                    logger.warning("Token verification via google-auth failed: %s; trying userinfo.", v_err)

            if (not email or not google_id) and access_token:
                uinfo_resp = requests.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10
                )
                if uinfo_resp.status_code == 200:
                    uinfo = uinfo_resp.json()
                    email = uinfo.get("email")
                    google_id = uinfo.get("sub")
                    name = uinfo.get("name") or uinfo.get("given_name") or ""

            if not email or not google_id:
                flash("Could not retrieve profile information from Google.", "err")
                return redirect(url_for("login"))

        except Exception as exc:
            logger.error("Google OAuth token exchange exception: %s", exc)
            flash("An error occurred during Google sign-in. Please try again.", "err")
            return redirect(url_for("login"))

    else:  # POST request
        data = request.get_json(silent=True) or request.form.to_dict()
        credential = data.get("credential") or data.get("id_token")
        email = data.get("email")
        google_id = data.get("google_id") or data.get("sub")
        name = data.get("name") or ""

        if credential:
            try:
                from google.oauth2 import id_token
                from google.auth.transport import requests as google_requests
                idinfo = id_token.verify_oauth2_token(
                    credential,
                    google_requests.Request(),
                    Config.GOOGLE_CLIENT_ID
                )
                email = idinfo.get("email")
                google_id = idinfo.get("sub")
                name = idinfo.get("name") or idinfo.get("given_name") or name
            except Exception as v_err:
                logger.error("Google ID token verification failed: %s", v_err)
                return jsonify(error="Invalid Google credential token.", code="invalid_token"), 400

        if not email or not google_id:
            return jsonify(error="Missing required Google OAuth credentials (email, google_id).", code="invalid_auth"), 400

    try:
        user = upsert_google_user(email=email, google_id=google_id, name=name)
        if not user.active:
            if request.method == "GET" or not (request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"):
                flash("This account is disabled. Contact support.", "err")
                return redirect(url_for("login"))
            return jsonify(error="This account is disabled. Contact support.", code="account_disabled"), 403

        dest = safe_next(session.get("google_auth_next")) or url_for("home")
        _sign_in(user)
        sync_upian_table(user)

        if request.method == "POST" and (request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"):
            return jsonify({
                "success": True,
                "user": user.to_dict(),
                "redirect": dest,
                "message": f"Successfully authenticated as {user.email}.",
            }), 200

        return redirect(dest)
    except Exception as exc:
        logger.error("Google OAuth upsert failed: %s", exc)
        if request.method == "GET":
            flash("Authentication failed. Please try again.", "err")
            return redirect(url_for("login"))
        return jsonify(error="Authentication failed. Please try again.", code="auth_failed"), 500


@app.post("/api/payment/webhook")
def api_payment_webhook():
    """
    Handles external payment gateway webhooks (e.g. Razorpay/Stripe).
    Validates cryptographic signature and applies plan updates idempotently.
    """
    sig = request.headers.get("X-Razorpay-Signature") or request.headers.get("X-Signature") or ""
    raw_payload = request.get_data()
    webhook_secret = env("RAZORPAY_WEBHOOK_SECRET") or Config.RAZORPAY_KEY_SECRET

    if webhook_secret:
        if not verify_webhook_signature(raw_payload, sig, webhook_secret):
            return jsonify(error="Invalid webhook signature.", code="invalid_signature"), 400

    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id") or data.get("id") or ("evt_" + uuid.uuid4().hex[:12])
    payload = data.get("payload", {}).get("payment", {}).get("entity", data)

    user_id = payload.get("user_id") or data.get("user_id")
    plan = payload.get("plan") or data.get("plan") or "plus"
    if plan == "pro":
        plan = "plus"
    elif plan == "pro_plus":
        plan = "plus_pro"

    tx_id = payload.get("id") or payload.get("transaction_id") or data.get("transaction_id")
    amount = float(payload.get("amount") or data.get("amount") or 15.0)
    currency = str(payload.get("currency") or data.get("currency") or "USD").upper()

    if not user_id or not tx_id:
        return jsonify(error="Missing user_id or transaction_id in webhook.", code="missing_params"), 400

    try:
        success, payment, msg = update_user_plan_idempotent(
            user_id=int(user_id),
            plan=plan,
            transaction_id=str(tx_id),
            amount=amount,
            currency=currency,
            payment_method="webhook",
            webhook_event_id=str(event_id),
        )
        return jsonify(success=success, message=msg, event_id=event_id), 200
    except Exception as exc:
        logger.error("Webhook processing error: %s", exc)
        return jsonify(error="Webhook processing failed.", code="webhook_error"), 500


def clean_history(raw):
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw[-Config.HISTORY_TURNS:]:
        if (isinstance(item, dict) and item.get("role") in ("user", "assistant")
                and isinstance(item.get("content"), str) and item["content"].strip()):
            role, content = item["role"], item["content"][:4000]
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n\n" + content
            else:
                out.append({"role": role, "content": content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    while out and out[-1]["role"] == "user":
        out.pop()  # the new prompt is appended by the caller
    return out


def is_image_request(prompt):
    """Detect all variants of image generation requests, including common typos."""
    p = prompt.strip().lower()
    patterns = [
        r"^(?:please\s+)?(?:can you\s+)?(?:generate|genrate|gerante|generte|create|creat|draw|paint|render|make)\s+(?:me\s+)?(?:a\s+)?(?:new\s+)?(?:an?\s+)?(?:image|img|picture|pic|photo|illustration|art)\s+(?:of\s+)?(.+)$",
        r"^(?:please\s+)?(?:can you\s+)?(?:generate|genrate|gerante|generte|create|creat|draw|paint|render|make)\s+(?:me\s+)?(?:a\s+)?(.+?)\s+(?:image|img|picture|pic|photo|illustration|art)$",
        r"^(?:please\s+)?(?:can you\s+)?(?:draw|paint)\s+(?:me\s+)?(?:an?\s+)?(.+)$",
        r"^(?:image|img|picture|pic|photo)\s+of\s+(.+)$",
        r"^(?:generate|genrate|gerante|generte|create|creat|render)\s+(?:an?\s+)?(?:image|img|picture|pic|photo)$",
    ]
    for pat in patterns:
        m = re.match(pat, p, re.I)
        if m:
            subj = m.group(1).strip() if m.lastindex else "requested image"
            if not re.search(r"\b(flowchart|diagram|architecture|er diagram|code|class diagram|table|sql|html)\b", subj):
                return True, subj
    return False, None


def is_collect_images_to_pdf_request(prompt, image_count=0):
    """Detect if user wants to compile/collect/convert uploaded images into a PDF or export document."""
    if image_count <= 0:
        return False
    p = prompt.strip().lower()

    if re.match(r"^\s*(?:what\s+is|what\s+are|explain\s+what|define)\s+.*?\bpdf\b", p):
        return False

    has_pdf = bool(re.search(r"\b(pdf|document|excel|xlsx|word|docx)\b", p))
    if not has_pdf:
        return False

    has_action = bool(re.search(
        r"\b(collect|combine|merge|convert|save|make|create|generate|put|join|turn|export|download|bundle|compile|take|arrange|order|extract)\b",
        p
    ))
    has_phrase = bool(re.search(
        r"\b(?:into|in|to|as)\s+(?:an?\s+)?(?:new\s+)?(?:pdf|document|excel|word)\b|"
        r"\b(?:pdf|document)\s+(?:file|document|version|format)\b|"
        r"\b(?:all|ss|photos?|images?|screenshots?|pictures?)\s+(?:into|in|to)\s+pdf\b|"
        r"\b(?:all|these|them|word|words|info|information)\s+(?:into|in|to)\b",
        p
    ))

    return has_action or has_phrase or (len(p.split()) <= 4 and has_pdf)


def ndjson(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


@app.post("/api/chat/stop")
@login_required
def api_chat_stop():
    data = request.get_json(silent=True) or {}
    req_id = data.get("request_id")
    if req_id:
        with ACTIVE_CHAT_LOCK:
            if req_id in ACTIVE_CHAT_REQUESTS:
                ACTIVE_CHAT_REQUESTS[req_id]["stopped"] = True
    return jsonify(success=True, stopped=True)


@app.post("/api/chat")
@login_required
def api_chat():
    data = request.get_json(silent=True) or {}
    request_id = str(data.get("request_id") or uuid.uuid4())
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify(error="Write a prompt first."), 400
    prompt = prompt.strip()
    if len(prompt) > Config.MAX_PROMPT_CHARS:
        return jsonify(error="Prompt is too long. The limit is %d characters." % Config.MAX_PROMPT_CHARS), 400

    user = g.user
    with ACTIVE_CHAT_LOCK:
        ACTIVE_CHAT_REQUESTS[request_id] = {"stopped": False, "user_id": user.id}
    usage = get_usage(user)
    if usage["day"]["used"] >= usage["day"]["limit"]:
        return jsonify(
            error="You have used all %d requests for today on the %s plan. The count resets at midnight (%s)."
                  % (usage["day"]["limit"], usage["plan_label"], Config.APP_TIMEZONE),
            code="daily_limit", usage=usage), 429
    if usage["month"]["used"] >= usage["month"]["limit"]:
        return jsonify(
            error="You have used all %d requests for this month on the %s plan. Upgrade or wait for the monthly reset."
                  % (usage["month"]["limit"], usage["plan_label"]),
            code="monthly_limit", usage=usage), 429

    # Resolve uploaded files and images from tokens
    raw_img_tokens = data.get("image_tokens") or []
    if not raw_img_tokens and data.get("image_token"):
        raw_img_tokens = [data.get("image_token")]
    img_tokens = [str(t) for t in raw_img_tokens if t and re.fullmatch(r"[0-9a-fA-F]{32}", str(t))]

    raw_up_tokens = data.get("uploaded_tokens") or []
    up_tokens = [str(t) for t in raw_up_tokens if t and re.fullmatch(r"[0-9a-fA-F]{32}", str(t))]

    all_tokens = []
    for t in img_tokens + up_tokens:
        if t not in all_tokens:
            all_tokens.append(t)

    user_dir = os.path.join(Config.UPLOAD_DIR, str(user.id))
    uploaded_images = []
    uploaded_docs = []
    if os.path.exists(user_dir):
        for token in all_tokens:
            matches = [f for f in os.listdir(user_dir) if f.startswith(token) and not f.endswith("_thumb.jpg")]
            if matches:
                fn = matches[0]
                ext = os.path.splitext(fn)[1].lower()
                p = os.path.join(user_dir, fn)
                if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".jfif", ".tif", ".tiff"}:
                    uploaded_images.append({"token": token, "filename": fn, "path": p, "ext": ext})
                else:
                    uploaded_docs.append({"token": token, "filename": fn, "path": p, "ext": ext})

    # Attached vault files: add the text of the chosen files (only the user's own) to the question
    files_used = []
    model_prompt = prompt
    doc_ids = [i for i in (data.get("doc_ids") or []) if isinstance(i, int)][:10]
    if doc_ids:
        files = read_docs_text(user.id, doc_ids)
        if files:
            model_prompt, _ = filetools.build_prompt(prompt, files, Config.FILE_MAX_CONTEXT_CHARS)
            files_used = [name for name, _t in files]

    raw_history = data.get("history")
    messages = clean_history(raw_history) + [{"role": "user", "content": model_prompt}]

    def stream():
        try:
            started = time.perf_counter()

            # 1. Compile uploaded screenshots or photos into PDF (with intelligent OCR text extraction & arrangement)
            if uploaded_images and is_collect_images_to_pdf_request(prompt, len(uploaded_images)):
                p_lower = prompt.lower()
                img_paths = [im["path"] for im in uploaded_images]
                count = len(uploaded_images)
                noun = "screenshot" if ("ss" in p_lower or "screenshot" in p_lower) else "photo" if "photo" in p_lower else "image"
                noun_str = f"{count} {noun}s" if count > 1 else f"{count} {noun}"

                # Detect requested export format: pdf (default), xlsx, docx
                export_fmt = "pdf"
                if re.search(r"\b(excel|xlsx|spreadsheet|sheet)\b", p_lower):
                    export_fmt = "xlsx"
                elif re.search(r"\b(word|docx|doc)\b", p_lower):
                    export_fmt = "docx"

                slug_name = "arranged_document" if any(w in p_lower for w in ["info", "word", "text", "order", "arrange", "extract", "content", "data", "summary"]) else "collected_screenshots"

                # Check if user explicitly asked for raw unextracted photos/gallery
                is_raw_gallery = bool(re.search(r"\b(raw|gallery|just images|only images|without text|as pictures|as photos)\b", p_lower))

                has_vision_api = bool(env("GEMINI_API_KEY") or getattr(Config, "GEMINI_API_KEY", None) or getattr(Config, "VISION_QWEN_3B_API_KEY", None))

                # If intelligent OCR information arrangement is applicable
                if not is_raw_gallery and has_vision_api:
                    primary = uploaded_images[0]
                    with open(primary["path"], "rb") as fh:
                        primary_bytes = fh.read()
                    primary_mime = "image/png" if primary["ext"] == ".png" else ("image/webp" if primary["ext"] == ".webp" else "image/jpeg")

                    img_tuples = []
                    for im in uploaded_images:
                        m = "image/png" if im["ext"] == ".png" else ("image/webp" if im["ext"] == ".webp" else "image/jpeg")
                        with open(im["path"], "rb") as fh:
                            img_tuples.append((fh.read(), m))

                    extract_prompt = (
                        "You are an expert document reconstruction, OCR, and information arrangement specialist.\n\n"
                        "TASK:\n"
                        "1. Thoroughly read and extract ALL text, words, numbers, labels, tables, and information visible across the provided screenshot(s)/image(s).\n"
                        "2. Do NOT summarize away or omit any words or details. Capture all visible information fully.\n"
                        "3. Organize and arrange all extracted words and data into a clean, logical, and well-structured order.\n\n"
                        "DOCUMENT STRUCTURE REQUIREMENTS:\n"
                        "- Meaningful Document Title (# Title)\n"
                        "- Logical Headings & Subheadings (## Section Name, ### Sub-section)\n"
                        "- Bullet points (- Item) or numbered steps (1. Step) for lists\n"
                        "- Clean Markdown tables (| Header 1 | Header 2 |) for tabular or comparison data\n"
                        "- A Key Summary / Highlights section if appropriate\n"
                        "- Fix broken OCR line wraps and arrange the words in natural, coherent reading order.\n"
                        "- Output ONLY the structured document text ready for PDF generation."
                    )
                    clean_instruction = prompt.strip()
                    if len(clean_instruction) > 10 and not re.match(r"^\s*(?:collect\s+all\s+(?:in\s*to|into)\s+(?:new\s+)?pdf|make\s+new\s+pdf|make\s+pdf)\s*$", clean_instruction, re.I):
                        extract_prompt += f"\n\nUSER SPECIFIC REQUIREMENTS:\n{clean_instruction}"

                    try:
                        v_res = vision.route(primary_bytes, mime=primary_mime, question=extract_prompt, images=img_tuples if len(img_tuples) > 1 else None)
                        arranged_text = (v_res.get("answer") or "").strip()
                        if arranged_text:
                            export_info = save_export(user.id, arranged_text, fmt=export_fmt, name=slug_name)
                            row = RequestLog(
                                user_id=user.id, prompt=prompt[:200], tier="weak", classifier="vision-pdf",
                                provider=v_res.get("engine", "vision")[:20],
                                model="ocr-document-compiler", slot="vision", status="success",
                                latency_ms=int((time.perf_counter() - started) * 1000),
                            )
                            db.session.add(row)
                            db.session.commit()

                            yield ndjson({
                                "event": "route", "tier": "simple", "legacy_tier": "weak", "score": 0, "classifier": "vision-pdf",
                                "classify_ms": 0, "downgraded": False, "model": "ocr-document-compiler",
                                "provider": v_res.get("engine", "vision"), "capabilities": ["multimodal-ocr", "document-structuring", "file-export"]
                            })

                            reply_text = (
                                f"✅ **Extracted and Arranged All Information into a New {export_fmt.upper()}!**\n\n"
                                f"All words and information from your {noun_str} have been extracted, organized in logical order, and compiled into a clean, professional document.\n\n"
                                f"- 📄 [**Download {export_info['filename']}** ({round(export_info['size'] / 1024, 1)} KB)]({export_info['url']})\n\n"
                                f"---\n\n"
                                f"### 📋 Arranged Document Preview\n\n"
                                f"{arranged_text}"
                            )

                            yield ndjson({
                                "event": "done",
                                "reply": reply_text,
                                "log": row.to_dict(),
                                "usage": get_usage(user),
                                "files_used": [im["filename"] for im in uploaded_images],
                                "file": export_info,
                                "files": [export_info],
                            })
                            return
                    except Exception as exc:
                        app.logger.warning("Vision OCR arrangement failed, falling back to raw image PDF: %s", exc)

                # Fallback to direct raw image compilation if OCR is unavailable or user asked for raw images
                try:
                    export_info = save_images_pdf(user.id, img_paths, name=slug_name)
                    row = RequestLog(
                        user_id=user.id, prompt=prompt[:200], tier="weak", classifier="rule",
                        provider="local", model="pdf-compiler", slot="local", status="success",
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                    db.session.add(row)
                    db.session.commit()

                    yield ndjson({
                        "event": "route", "tier": "simple", "legacy_tier": "weak", "score": 0, "classifier": "rule",
                        "classify_ms": 0, "downgraded": False, "model": "pdf-compiler",
                        "provider": "local", "capabilities": ["image-to-pdf", "file-export"]
                    })

                    reply_text = (
                        f"✅ **Collected {noun_str} into a PDF document!**\n\n"
                        f"All {count} of your attached {noun}s have been compiled into a high-quality multi-page PDF.\n\n"
                        f"- 📄 [**Download {export_info['filename']}** ({round(export_info['size'] / 1024, 1)} KB)]({export_info['url']})"
                    )

                    yield ndjson({
                        "event": "done",
                        "reply": reply_text,
                        "log": row.to_dict(),
                        "usage": get_usage(user),
                        "files_used": [im["filename"] for im in uploaded_images],
                        "file": export_info,
                        "files": [export_info],
                    })
                    return
                except Exception as exc:
                    app.logger.exception("Failed to compile images to PDF")
                    yield ndjson({"event": "error", "error": f"Could not create PDF from uploaded images: {str(exc)}"})
                    return

            # Real-data answers (gold/silver prices) and file exports (excel, pdf, word...) skip the AI model.
            try:
                sp = special.handle(user.id, prompt, raw_history) if (not files_used and not uploaded_images) else None
            except special.SpecialError as exc:
                row = RequestLog(user_id=user.id, prompt=prompt[:200], tier="weak", classifier="rule",
                                 provider="local", model="market-data", slot="local", status="error",
                                 error=str(exc)[:1000])
                db.session.add(row)
                db.session.commit()
                yield ndjson({"event": "error", "error": str(exc), "log": row.to_dict(), "usage": get_usage(user)})
                return
            if sp:
                row = RequestLog(user_id=user.id, prompt=prompt[:200], tier="weak", classifier="rule",
                                 provider="local", model=sp["source"], slot="local", status="success",
                                 latency_ms=int((time.perf_counter() - started) * 1000))
                db.session.add(row)
                db.session.commit()
                yield ndjson({"event": "route", "tier": "weak", "score": 0, "classifier": "rule", "classify_ms": 0,
                              "downgraded": False, "model": sp["source"], "provider": "local"})
                yield ndjson({"event": "done", "reply": sp["reply"], "log": row.to_dict(), "usage": get_usage(user),
                              "files_used": [], "file": sp["file"], "files": sp["files"]})
                return

            # Fast Casual & Greeting Short-Circuit: answer frequent pleasantries concisely with ultra-low latency (<5ms)
            is_casual, casual_reply = llm.is_casual_greeting(prompt)
            if is_casual and not uploaded_images and not files_used:
                row = RequestLog(
                    user_id=user.id, prompt=prompt[:200], tier="weak", classifier="instant-greeting",
                    provider="local", model="casual-fast-response", slot="local", status="success",
                    latency_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    tokens_in=len(prompt.split()), tokens_out=len(casual_reply.split()),
                )
                db.session.add(row)
                db.session.commit()
                yield ndjson({
                    "event": "route", "tier": "simple", "legacy_tier": "weak", "score": 0.0,
                    "classifier": "instant-greeting", "classify_ms": 1, "downgraded": False,
                    "model": "casual-fast-response", "provider": "local", "capabilities": ["conversation"],
                    "estimated_cost": "free", "latency_tier": "instant (<10ms)"
                })
                yield ndjson({
                    "event": "done",
                    "reply": casual_reply,
                    "log": row.to_dict(),
                    "usage": get_usage(user),
                    "files_used": [],
                })
                return

            # Multimodal Vision: if uploaded images are passed with a question
            if uploaded_images:
                primary = uploaded_images[0]
                with open(primary["path"], "rb") as fh:
                    img_bytes = fh.read()
                mime = "image/png" if primary["ext"] == ".png" else ("image/webp" if primary["ext"] == ".webp" else "image/jpeg")

                img_tuples = []
                for im in uploaded_images:
                    m = "image/png" if im["ext"] == ".png" else ("image/webp" if im["ext"] == ".webp" else "image/jpeg")
                    with open(im["path"], "rb") as fh:
                        img_tuples.append((fh.read(), m))

                try:
                    v_res = vision.route(img_bytes, mime=mime, question=prompt, images=img_tuples if len(img_tuples) > 1 else None)
                    row = RequestLog(
                        user_id=user.id, prompt=prompt[:200], tier="weak", classifier="vision",
                        provider=v_res.get("engine", "vision")[:20],
                        model=v_res.get("model", "qwen2.5-vl")[:80],
                        slot="vision", status="success",
                        latency_ms=v_res.get("latency_ms", int((time.perf_counter() - started) * 1000)),
                    )
                    db.session.add(row)
                    db.session.commit()
                    yield ndjson({
                        "event": "route", "tier": "multimodal-vision", "score": 0.65, "classifier": "vision",
                        "classify_ms": 10, "downgraded": False, "model": v_res.get("model", "vision"),
                        "provider": v_res.get("engine", "vision"), "capabilities": ["multimodal-vision"],
                    })
                    yield ndjson({
                        "event": "done",
                        "reply": v_res.get("answer") or v_res.get("top_prediction") or "Visual analysis complete.",
                        "vision": v_res, "log": row.to_dict(), "usage": get_usage(user),
                        "files_used": [im["filename"] for im in uploaded_images],
                    })
                    return
                except RuntimeError as exc:
                    row = RequestLog(
                        user_id=user.id, prompt=prompt[:200], tier="weak", classifier="vision",
                        provider="system", model="vision-guidance", slot="vision", status="error",
                        error=str(exc)[:1000]
                    )
                    db.session.add(row)
                    db.session.commit()
                    notice = (
                        "⚠️ **Visual AI Analysis Unavailable**\n\n"
                        f"{str(exc)}\n\n"
                        "To ask questions about screenshots and photos, please configure `GEMINI_API_KEY` in your `.env` file.\n\n"
                        "💡 *Tip: You can collect and convert your screenshots/photos into a PDF anytime without an API key by saying **'collect all into pdf'**.*"
                    )
                    yield ndjson({"event": "route", "tier": "simple", "score": 0, "classifier": "system",
                                  "classify_ms": 0, "downgraded": False, "model": "vision-guidance", "provider": "system"})
                    yield ndjson({"event": "done", "reply": notice, "log": row.to_dict(), "usage": get_usage(user),
                                  "files_used": [im["filename"] for im in uploaded_images]})
                    return

            # Image Generation Routing: if prompt requests image generation
            is_img, img_subject = is_image_request(prompt)
            if is_img:
                img_prompt = img_subject
                user_plan_info = PLANS.get(user.plan, PLANS["free"])
                # 1. Plan check: Free plan cannot generate images
                if not user_plan_info.get("image_gen", False):
                    row = RequestLog(
                        user_id=user.id, prompt=prompt[:200], tier="weak", classifier="image-gen",
                        provider="image-gen", model="plan-upgrade-required", slot="image-gen", status="error",
                        error="Plan upgrade required for image generation",
                    )
                    db.session.add(row)
                    db.session.commit()
                    yield ndjson({
                        "event": "upgrade_required",
                        "feature": "image-gen",
                        "title": "AI Image Generation is a Pro Feature",
                        "current_plan": user.plan,
                        "message": "You are currently on the Free plan. Free users cannot generate AI images. Please upgrade to Pro (20 AI images/day) or Pro Plus to create images.",
                        "log": row.to_dict(),
                        "usage": get_usage(user),
                        "files_used": [],
                    })
                    return

                if usage["images"]["used"] >= usage["images"]["limit"]:
                    msg = ("You have used today's image generation allowance for the %s plan (%s of %s). "
                           "It resets at midnight (%s)." % (user_plan_info["label"], usage["images"]["used"],
                           usage["images"]["limit"], Config.APP_TIMEZONE))
                    yield ndjson({"event": "done", "reply": msg, "usage": usage, "files_used": []})
                    return

                try:
                    gen_res = image_gen.generate_image(img_prompt, user_id=user.id)
                    row = RequestLog(
                        user_id=user.id, prompt=prompt[:200], tier="strong", classifier="image-gen",
                        provider=gen_res["provider"][:20], model=gen_res["model"][:80], slot="image-gen",
                        status="success", latency_ms=gen_res.get("latency_ms", int((time.perf_counter() - started) * 1000)),
                    )
                    db.session.add(row)
                    db.session.commit()
                    yield ndjson({
                        "event": "route", "tier": "image-gen", "score": gen_res.get("complexity_score", 0.5),
                        "classifier": "image-gen", "classify_ms": 5, "downgraded": False,
                        "model": gen_res["model"], "provider": gen_res["provider"], "capabilities": ["image-generation"],
                    })
                    yield ndjson({
                        "event": "done",
                        "reply": f"Generated image for: **{img_prompt}**",
                        "image": gen_res, "log": row.to_dict(), "usage": get_usage(user),
                        "files_used": [],
                    })
                    return
                except image_gen.ImageGenError as exc:
                    row = RequestLog(
                        user_id=user.id, prompt=prompt[:200], tier="weak", classifier="image-gen",
                        provider="image-gen", model="none", slot="image-gen", status="error",
                        error=exc.message[:1000],
                    )
                    db.session.add(row)
                    db.session.commit()
                    # User-friendly notification when API key is not available
                    err_msg = (
                        f"⚠️ **Image Generation Notice**\n\n"
                        f"{exc.message}"
                    )
                    yield ndjson({
                        "event": "done",
                        "reply": err_msg,
                        "log": row.to_dict(),
                        "usage": get_usage(user),
                        "files_used": [],
                    })
                    return

            pref = data.get("preference") or getattr(Config, "ROUTING_PREFERENCE", "auto")
            route = classifier.classify(prompt, preference=pref)
            tier, downgraded = route["tier"], False
            legacy_tier = route.get("legacy_tier", "strong" if tier in ("complex", "strong") else "weak")
            if tier in ("complex", "strong") and usage["strong"]["used"] >= usage["strong"]["limit"]:
                tier, downgraded = "medium", True
                legacy_tier = "weak"
            chain = llm.build_chain(tier)

            def log(**extra):
                row = RequestLog(
                    user_id=user.id, prompt=prompt[:200],
                    tier=legacy_tier,
                    complexity=route["score"],
                    classifier=route["method"], classify_ms=route["classify_ms"],
                    downgraded=downgraded, **extra)
                db.session.add(row)
                db.session.commit()
                return row

            if not chain:
                notice = (
                    "👋 **Welcome to Upian AI!**\n\n"
                    "No AI model API key is currently configured in `.env`.\n\n"
                    "• **To activate AI chat**: Add your Gemini key (`GEMINI_API_KEY=...`) or OpenAI key (`OPENAI_API_KEY=...`) in the `.env` file.\n"
                    "• **Active right now without any key**:\n"
                    "  - Real-time crypto & market quotes (e.g. `bitcoin price`, `usd to inr`, `nifty 50`)\n"
                    "  - Historical gold & silver data (e.g. `gold price in 2024`)\n"
                    "  - Real weather observations & forecasts (e.g. `weather in Mumbai`)\n"
                    "  - Instant file conversions & exports (e.g. `in excel`, `in pdf`, `in docx`)\n"
                    "  - Local vision classification on the [Vision page](/vision)"
                )
                row = log(status="success", provider="system", model="welcome-guide", slot="system")
                yield ndjson({"event": "route", "tier": "simple", "legacy_tier": "weak", "score": 0,
                              "classifier": "system", "classify_ms": 0, "downgraded": False,
                              "model": "welcome-guide", "provider": "system", "capabilities": ["system"],
                              "estimated_cost": "free", "latency_tier": "instant"})
                yield ndjson({"event": "done", "reply": notice, "log": row.to_dict(), "usage": get_usage(user), "files_used": []})
                return

            yield ndjson({"event": "route", "tier": tier, "legacy_tier": legacy_tier,
                          "score": route["score"], "classifier": route["method"],
                          "classify_ms": route["classify_ms"], "downgraded": downgraded,
                          "model": chain[0]["model"], "provider": chain[0]["provider"],
                          "capabilities": route.get("capabilities", []),
                          "estimated_cost": route.get("estimated_cost", "low"),
                          "latency_tier": route.get("latency_tier", "")})

            errors, result, used = [], None, None
            for slot in chain:
                try:
                    result = llm.generate(slot, messages)
                    used = slot
                    break
                except llm.ProviderError as exc:
                    errors.append("%s: %s" % (slot["name"], exc))

            if result is None:
                try:
                    mock_slot = {"name": "mock_emergency", "provider": "mock", "model": "gemini-3.8-flash", "api_key": "", "base_url": ""}
                    result = llm.generate(mock_slot, messages)
                    used = mock_slot
                except Exception:
                    pass

            if result is None:
                row = log(status="error", error=" | ".join(errors)[:1000], provider=chain[0]["provider"],
                          model=chain[0]["model"], slot=chain[0]["name"], fallback=len(chain) > 1)
                yield ndjson({"event": "error", "error": "The model did not respond. Try again in a moment. This request was not counted.",
                              "log": row.to_dict(), "usage": get_usage(user)})
                return

            reply_text = result["text"]

            # Stream out tokens token-by-token
            tokens = re.findall(r'\S+\s*|\s+', reply_text)
            if not tokens:
                tokens = [reply_text]

            stopped_early = False
            for t in tokens:
                with ACTIVE_CHAT_LOCK:
                    if ACTIVE_CHAT_REQUESTS.get(request_id, {}).get("stopped", False):
                        stopped_early = True
                        break
                yield ndjson({"event": "chunk", "delta": t})
                time.sleep(0.012)

            if stopped_early:
                row = log(status="stopped", provider=used["provider"] if used else "unknown", model=used["model"] if used else "stopped", slot=used["name"] if used else "")
                yield ndjson({"event": "error", "error": "Generation stopped by user.", "log": row.to_dict(), "usage": get_usage(user)})
                return

            fallback = bool(errors) or (tier == "strong" and used["name"] == "free")

            row = log(status="success", provider=used["provider"], model=used["model"], slot=used["name"],
                      fallback=fallback, tokens_in=result["tokens_in"], tokens_out=result["tokens_out"],
                      latency_ms=result["latency_ms"])
            yield ndjson({"event": "done", "reply": reply_text, "log": row.to_dict(), "usage": get_usage(user),
                          "files_used": files_used})
        except GeneratorExit:
            app.logger.info("Client disconnected / aborted chat request: %s", request_id)
            try:
                row = RequestLog(
                    user_id=user.id, prompt=prompt[:200],
                    tier=legacy_tier if 'legacy_tier' in locals() else "weak",
                    classifier=route["method"] if 'route' in locals() else "aborted",
                    status="stopped", latency_ms=int((time.perf_counter() - started) * 1000)
                )
                db.session.add(row)
                db.session.commit()
            except Exception:
                db.session.rollback()
            raise
        except Exception:
            app.logger.exception("chat failed")
            db.session.rollback()
            yield ndjson({"event": "error", "error": "Something went wrong on our side. Try again."})
        finally:
            with ACTIVE_CHAT_LOCK:
                ACTIVE_CHAT_REQUESTS.pop(request_id, None)

    return Response(stream_with_context(stream()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def user_summary(user):
    w = windows()
    base = db.session.query(RequestLog).filter(
        RequestLog.user_id == user.id, RequestLog.status == "success", RequestLog.created_at >= w["day_start"])
    n, tokens, avg = base.with_entities(
        func.count(RequestLog.id),
        func.coalesce(func.sum(RequestLog.tokens_in + RequestLog.tokens_out), 0),
        func.avg(RequestLog.latency_ms)).one()
    strong = base.filter(RequestLog.tier == "strong").with_entities(func.count(RequestLog.id)).scalar() or 0
    return {"requests": n, "tokens": int(tokens), "avg_latency_ms": int(avg) if avg else None, "strong": strong}


@app.get("/api/activity")
@login_required
def api_activity():
    limit = min(max(request.args.get("limit", 20, type=int), 1), 100)
    before = request.args.get("before", type=int)
    query = RequestLog.query.filter_by(user_id=g.user.id)
    if before:
        query = query.filter(RequestLog.id < before)
    rows = query.order_by(RequestLog.id.desc()).limit(limit + 1).all()
    return jsonify(items=[r.to_dict() for r in rows[:limit]], has_more=len(rows) > limit,
                   summary=user_summary(g.user), usage=get_usage(g.user))


@app.get("/api/usage")
@login_required
def api_usage():
    return jsonify(get_usage(g.user))


# ----------------------------------------------------------------------------- admin
def admin_stats():
    now = utcnow()
    w = windows(now)
    Q = db.session.query
    ok = RequestLog.status == "success"
    today = RequestLog.created_at >= w["day_start"]
    month = RequestLog.created_at >= w["month_start"]
    tokens = RequestLog.tokens_in + RequestLog.tokens_out

    def count(*conds):
        return Q(func.count(RequestLog.id)).select_from(RequestLog).filter(*conds).scalar() or 0

    totals = {
        "users": Q(func.count(User.id)).scalar() or 0,
        "new_week": Q(func.count(User.id)).filter(User.created_at >= now - timedelta(days=7)).scalar() or 0,
        "active_today": Q(func.count(func.distinct(RequestLog.user_id))).select_from(RequestLog).filter(today).scalar() or 0,
        "requests_today": count(ok, today),
        "requests_month": count(ok, month),
        "tokens_today": int(Q(func.coalesce(func.sum(tokens), 0)).select_from(RequestLog).filter(ok, today).scalar()),
        "tokens_month": int(Q(func.coalesce(func.sum(tokens), 0)).select_from(RequestLog).filter(ok, month).scalar()),
        "avg_latency_ms": None,
        "errors_today": count(RequestLog.status == "error", today),
        "strong_today": count(ok, today, RequestLog.tier == "strong"),
        "fallback_today": count(ok, today, RequestLog.fallback.is_(True)),
    }
    avg = Q(func.avg(RequestLog.latency_ms)).select_from(RequestLog).filter(ok, today).scalar()
    totals["avg_latency_ms"] = int(avg) if avg else None

    plan_rows = dict(Q(User.plan, func.count(User.id)).group_by(User.plan).all())
    plans = [{"plan": p, "label": PLANS[p]["label"], "count": plan_rows.get(p, 0)} for p in PLAN_ORDER]

    tz = local_tz()
    local_today = datetime.now(timezone.utc).astimezone(tz).date()
    dates = [local_today - timedelta(days=i) for i in range(13, -1, -1)]
    start = datetime.combine(dates[0], dtime.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    buckets = {d: {"date": d.isoformat(), "strong": 0, "weak": 0} for d in dates}
    for created, tier in Q(RequestLog.created_at, RequestLog.tier).filter(ok, RequestLog.created_at >= start).all():
        d = created.replace(tzinfo=timezone.utc).astimezone(tz).date()
        if d in buckets:
            buckets[d]["strong" if tier == "strong" else "weak"] += 1

    models = [
        {"provider": p, "model": m, "count": c, "avg_latency_ms": int(a) if a else None, "tokens": int(t)}
        for p, m, c, a, t in Q(RequestLog.provider, RequestLog.model, func.count(RequestLog.id),
                               func.avg(RequestLog.latency_ms), func.coalesce(func.sum(tokens), 0))
        .filter(ok, month).group_by(RequestLog.provider, RequestLog.model)
        .order_by(func.count(RequestLog.id).desc()).limit(8).all()
    ]
    top_users = [
        {"name": n, "email": e, "count": c, "tokens": int(t)}
        for n, e, c, t in Q(User.name, User.email, func.count(RequestLog.id), func.coalesce(func.sum(tokens), 0))
        .join(RequestLog, RequestLog.user_id == User.id).filter(ok, today)
        .group_by(User.id, User.name, User.email).order_by(func.count(RequestLog.id).desc()).limit(6).all()
    ]
    recent = []
    for row in RequestLog.query.options(joinedload(RequestLog.user)).order_by(RequestLog.id.desc()).limit(12).all():
        item = row.to_dict()
        item["user"] = row.user.email if row.user else "-"
        recent.append(item)

    return {
        "generated_at": iso(now), "threshold": Config.CLASSIFIER_THRESHOLD,
        "classifier": {"ready": classifier.ready, "status": classifier.status},
        "slots": llm.slots_status(), "totals": totals, "plans": plans,
        "series": list(buckets.values()), "models": models, "top_users": top_users, "recent": recent,
    }


@app.get("/admin")
@admin_required
def admin_home():
    return render_template("admin_dashboard.html", active="overview")


@app.get("/admin/api/stats")
@admin_required
def admin_api_stats():
    return jsonify(admin_stats())


@app.get("/admin/users")
@admin_required
def admin_users():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    query = User.query.filter(User.deleted_at.is_(None))
    if q:
        like = "%" + q + "%"
        query = query.filter(db.or_(User.email.ilike(like), User.name.ilike(like)))
    pag = query.order_by(User.id.desc()).paginate(page=page, per_page=20, error_out=False)
    ids = [u.id for u in pag.items]
    w = windows()

    def counts(since):
        if not ids:
            return {}
        rows = (db.session.query(RequestLog.user_id, func.count(RequestLog.id))
                .filter(RequestLog.user_id.in_(ids), RequestLog.status == "success", RequestLog.created_at >= since)
                .group_by(RequestLog.user_id).all())
        return dict(rows)

    all_plans = plans_service.get_all_plans_for_admin()
    return render_template("admin_users.html", active="users", pag=pag, q=q, plan_order=PLAN_ORDER,
                           all_plans=all_plans, day_counts=counts(w["day_start"]), month_counts=counts(w["month_start"]))


@app.post("/admin/users/<int:uid>/edit")
@admin_required
def admin_edit_user(uid):
    user = db.get_or_404(User, uid)
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    plan = (request.form.get("plan") or "").strip()
    status = request.form.get("status")

    if not name:
        flash("User name cannot be empty.", "err")
        return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        flash("Invalid email address.", "err")
        return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))

    existing = User.query.filter(User.email == email, User.id != uid).first()
    if existing:
        flash(f"Email '{email}' is already in use by another user.", "err")
        return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))

    valid_plan = (plan in PLANS) or bool(Plan.query.filter_by(code=plan, is_active=True).first())
    if plan and not valid_plan:
        flash(f"Plan '{plan}' is not a valid plan.", "err")
        return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))

    old_data = {"name": user.name, "email": user.email, "plan": user.plan, "active": user.active}
    user.name = name
    user.email = email
    if plan:
        user.plan = plan
    if status is not None:
        user.active = status in ("1", "true", "active", "on", True)

    audit = AuditLog(
        admin_id=g.user.id,
        target_user_id=user.id,
        action="edit_user",
        details=json.dumps({"old": old_data, "new": {"name": user.name, "email": user.email, "plan": user.plan, "active": user.active}}),
    )
    db.session.add(audit)
    db.session.commit()
    sync_upian_table(user)
    flash(f"User {user.email} updated successfully.", "ok")
    return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))


@app.post("/admin/users/<int:uid>/delete")
@admin_required
def admin_delete_user(uid):
    user = db.get_or_404(User, uid)
    if user.id == g.user.id:
        flash("You cannot delete your own administrative account.", "err")
        return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))

    user.deleted_at = utcnow()
    user.active = False
    audit = AuditLog(
        admin_id=g.user.id,
        target_user_id=user.id,
        action="delete_user",
        details=json.dumps({"email": user.email, "name": user.name, "plan": user.plan}),
    )
    db.session.add(audit)
    db.session.commit()
    flash(f"User {user.email} was successfully deleted.", "ok")
    return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))


@app.post("/admin/users/<int:uid>/cancel-plan")
@admin_required
def admin_cancel_user_plan(uid):
    user = db.get_or_404(User, uid)
    def_plan = plans_service.get_default_plan()
    def_code = def_plan.code if def_plan else "free"

    old_plan = user.plan
    user.plan = def_code
    user.plan_status = "cancelled"
    user.subscription_id = None
    user.plan_end = utcnow()

    audit = AuditLog(
        admin_id=g.user.id,
        target_user_id=user.id,
        action="cancel_user_plan",
        details=json.dumps({"old_plan": old_plan, "new_plan": def_code, "user_email": user.email}),
    )
    db.session.add(audit)
    db.session.commit()
    sync_upian_table(user)
    flash(f"Cancelled subscription for {user.email}. Switched to {def_plan.label if def_plan else def_code}.", "ok")
    return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))


@app.post("/admin/users/<int:uid>/plan")
@admin_required
def admin_set_plan(uid):
    user = db.get_or_404(User, uid)
    plan = request.form.get("plan")
    if plan not in PLANS:
        abort(400, description="Unknown plan.")
    user.plan = plan
    db.session.commit()
    flash("%s is now on the %s plan." % (user.email, PLANS[plan]["label"]))
    return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))


@app.post("/admin/users/<int:uid>/toggle")
@admin_required
def admin_toggle_user(uid):
    user = db.get_or_404(User, uid)
    if user.id == g.user.id:
        abort(400, description="You cannot disable your own account.")
    user.active = not user.active
    db.session.commit()
    flash("%s was %s." % (user.email, "enabled" if user.active else "disabled"))
    return redirect(safe_next(request.form.get("next")) or url_for("admin_users"))


@app.get("/admin/plans")
@admin_required
def admin_plans():
    import plans_service
    plans = plans_service.get_all_plans_for_admin()
    return render_template("admin_plans.html", active="plans", plans=plans)


@app.post("/admin/plans/create")
@admin_required
def admin_create_plan():
    import plans_service
    label = request.form.get("label", "").strip()
    code = request.form.get("code", "").strip()
    price_inr = request.form.get("price_inr", 0)
    price_usd = request.form.get("price_usd")
    price_inr_yearly = request.form.get("price_inr_yearly")
    price_usd_yearly = request.form.get("price_usd_yearly")
    period = request.form.get("period", "/mo").strip()
    description = request.form.get("description", "").strip()
    tag = request.form.get("tag", "").strip()
    daily = request.form.get("daily", 100)
    monthly = request.form.get("monthly", 2000)
    strong_monthly = request.form.get("strong_monthly", 400)
    image_gen = request.form.get("image_gen") in ("1", "true", "yes", "on")
    image_daily = request.form.get("image_daily", 20 if image_gen else 0)
    raw_features = request.form.get("features", "")

    features = [f.strip().lstrip("•-*✔ ").strip() for f in raw_features.split("\n") if f.strip()]

    ok, plan, msg = plans_service.create_new_plan(
        label=label,
        code=code,
        price_inr=price_inr,
        price_usd=price_usd,
        price_inr_yearly=price_inr_yearly,
        price_usd_yearly=price_usd_yearly,
        period=period,
        description=description,
        tag=tag,
        daily=daily,
        monthly=monthly,
        strong_monthly=strong_monthly,
        image_gen=image_gen,
        image_daily=image_daily,
        features=features,
    )
    if ok:
        flash(f"Plan '{plan.label}' created successfully!", "ok")
    else:
        flash(f"Error creating plan: {msg}", "err")
    return redirect(url_for("admin_plans"))


@app.post("/admin/plans/<int:pid>/update")
@admin_required
def admin_update_plan(pid):
    import plans_service
    label = request.form.get("label", "").strip()
    price_inr = request.form.get("price_inr", 0)
    price_usd = request.form.get("price_usd")
    price_inr_yearly = request.form.get("price_inr_yearly")
    price_usd_yearly = request.form.get("price_usd_yearly")
    period = request.form.get("period", "/mo").strip()
    description = request.form.get("description", "").strip()
    tag = request.form.get("tag", "").strip()
    daily = request.form.get("daily", 100)
    monthly = request.form.get("monthly", 2000)
    strong_monthly = request.form.get("strong_monthly", 400)
    image_gen = request.form.get("image_gen") in ("1", "true", "yes", "on")
    image_daily = request.form.get("image_daily", 20 if image_gen else 0)
    is_active = request.form.get("is_active") in ("1", "true", "yes", "on")
    raw_features = request.form.get("features", "")

    features = [f.strip().lstrip("•-*✔ ").strip() for f in raw_features.split("\n") if f.strip()]

    ok, plan, msg = plans_service.update_existing_plan(
        plan_id=pid,
        label=label,
        price_inr=price_inr,
        price_usd=price_usd,
        price_inr_yearly=price_inr_yearly,
        price_usd_yearly=price_usd_yearly,
        period=period,
        description=description,
        tag=tag,
        daily=daily,
        monthly=monthly,
        strong_monthly=strong_monthly,
        image_gen=image_gen,
        image_daily=image_daily,
        features=features,
        is_active=is_active,
    )
    if ok:
        flash(f"Plan '{plan.label}' updated successfully!", "ok")
    else:
        flash(f"Error updating plan: {msg}", "err")
    return redirect(url_for("admin_plans"))


@app.post("/admin/plans/<int:pid>/delete")
@admin_required
def admin_delete_plan(pid):
    import plans_service
    replacement_plan_id = request.form.get("replacement_plan_id")
    ok, msg = plans_service.remove_plan(pid, replacement_plan_id=replacement_plan_id, admin_id=g.user.id)
    if ok:
        flash(msg, "ok")
    else:
        flash(msg, "err")
    return redirect(url_for("admin_plans"))


@app.post("/admin/plans/<int:pid>/default")
@admin_required
def admin_set_default_plan(pid):
    import plans_service
    ok, msg = plans_service.set_plan_as_default(pid, admin_id=g.user.id)
    if ok:
        flash(msg, "ok")
    else:
        flash(msg, "err")
    return redirect(url_for("admin_plans"))


@app.get("/admin/requests")
@admin_required
def admin_requests():
    q = (request.args.get("q") or "").strip()
    tier = request.args.get("tier") or ""
    status = request.args.get("status") or ""
    page = request.args.get("page", 1, type=int)
    query = RequestLog.query.options(joinedload(RequestLog.user))
    if q:
        query = query.join(User, RequestLog.user_id == User.id).filter(User.email.ilike("%" + q + "%"))
    if tier in ("strong", "weak"):
        query = query.filter(RequestLog.tier == tier)
    if status in ("success", "error"):
        query = query.filter(RequestLog.status == status)
    pag = query.order_by(RequestLog.id.desc()).paginate(page=page, per_page=30, error_out=False)
    return render_template("admin_requests.html", active="requests", pag=pag, q=q, tier=tier, status=status,
                           threshold=Config.CLASSIFIER_THRESHOLD)


# ----------------------------------------------------------------------------- admin analytics
def percentile(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return int(vals[f] + (vals[c] - vals[f]) * (k - f))


def _avg(vals):
    return int(sum(vals) / len(vals)) if vals else None


def admin_analytics(days):
    days = days if days in (7, 30, 90) else 30
    now = utcnow()
    since = now - timedelta(days=days)
    w = windows(now)
    Q = db.session.query
    tz = local_tz()

    users = {u.id: u for u in User.query.all()}
    rows = Q(RequestLog.user_id, RequestLog.created_at, RequestLog.tier, RequestLog.status, RequestLog.tokens_in,
             RequestLog.tokens_out, RequestLog.latency_ms, RequestLog.fallback).filter(RequestLog.created_at >= since).all()

    def active_since(delta):
        return Q(func.count(func.distinct(RequestLog.user_id))).filter(RequestLog.created_at >= now - delta).scalar() or 0

    ok_rows = [r for r in rows if r.status == "success"]
    lat = [r.latency_ms for r in ok_rows if r.latency_ms]
    tin = sum(r.tokens_in for r in ok_rows)
    tout = sum(r.tokens_out for r in ok_rows)
    errors = len(rows) - len(ok_rows)

    totals = {
        "users": len(users),
        "active_users": len({r.user_id for r in rows}),
        "dau": active_since(timedelta(days=1)), "wau": active_since(timedelta(days=7)), "mau": active_since(timedelta(days=30)),
        "requests": len(ok_rows), "errors": errors,
        "error_rate": round(errors / len(rows) * 100, 1) if rows else 0,
        "tokens_in": tin, "tokens_out": tout, "tokens": tin + tout,
        "avg_tokens": int((tin + tout) / len(ok_rows)) if ok_rows else 0,
        "latency_avg": _avg(lat), "latency_p50": percentile(lat, 50), "latency_p95": percentile(lat, 95),
        "strong_pct": round(sum(1 for r in ok_rows if r.tier == "strong") / len(ok_rows) * 100) if ok_rows else 0,
        "fallback": sum(1 for r in ok_rows if r.fallback),
    }

    # per user + per plan
    per_user = {}
    for r in ok_rows:
        d = per_user.setdefault(r.user_id, {"requests": 0, "tokens": 0, "lat": []})
        d["requests"] += 1
        d["tokens"] += r.tokens_in + r.tokens_out
        if r.latency_ms:
            d["lat"].append(r.latency_ms)

    month_counts = dict(Q(RequestLog.user_id, func.count(RequestLog.id))
                        .filter(RequestLog.status == "success", RequestLog.created_at >= w["month_start"])
                        .group_by(RequestLog.user_id).all())

    plans = []
    for p in PLAN_ORDER:
        members = [u for u in users.values() if u.plan == p]
        ids = {u.id for u in members}
        reqs = sum(per_user[i]["requests"] for i in ids if i in per_user)
        toks = sum(per_user[i]["tokens"] for i in ids if i in per_user)
        lats = [x for i in ids if i in per_user for x in per_user[i]["lat"]]
        cap = len(members) * PLANS[p]["monthly"]
        used = sum(month_counts.get(i, 0) for i in ids)
        plans.append({"plan": p, "label": PLANS[p]["label"], "users": len(members),
                      "active": sum(1 for i in ids if i in per_user), "requests": reqs, "tokens": toks,
                      "avg_latency_ms": _avg(lats), "quota_used_pct": round(used / cap * 100) if cap else 0})

    top = []
    for uid, d in sorted(per_user.items(), key=lambda kv: kv[1]["tokens"], reverse=True)[:15]:
        u = users.get(uid)
        if not u:
            continue
        top.append({"name": u.name, "email": u.email, "plan": plans_service.get_user_plan_dict(u)["label"],
                    "requests": d["requests"], "tokens": d["tokens"], "avg_latency_ms": _avg(d["lat"])})

    near = []
    for u in users.values():
        limit = plans_service.get_user_plan_dict(u)["monthly"]
        used = month_counts.get(u.id, 0)
        if limit and used / limit >= 0.8:
            near.append({"name": u.name, "email": u.email, "plan": plans_service.get_user_plan_dict(u)["label"],
                         "used": used, "limit": limit})
    near.sort(key=lambda x: x["used"] / x["limit"], reverse=True)

    # daily series (local days)
    local_today = datetime.now(timezone.utc).astimezone(tz).date()
    dates = [local_today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    buckets = {d: {"date": d.isoformat(), "requests": 0, "tokens": 0, "users": set(), "lat": []} for d in dates}
    for r in rows:
        d = r.created_at.replace(tzinfo=timezone.utc).astimezone(tz).date()
        b = buckets.get(d)
        if not b:
            continue
        b["users"].add(r.user_id)
        if r.status == "success":
            b["requests"] += 1
            b["tokens"] += r.tokens_in + r.tokens_out
            if r.latency_ms:
                b["lat"].append(r.latency_ms)
    series = [{"date": b["date"], "requests": b["requests"], "tokens": b["tokens"], "users": len(b["users"]),
               "avg_latency_ms": _avg(b["lat"])} for b in buckets.values()]

    return {"generated_at": iso(now), "days": days, "totals": totals, "plans": plans, "top_users": top,
            "near_limit": near[:15], "series": series}


@app.get("/admin/analytics")
@admin_required
def admin_analytics_page():
    return render_template("admin_analytics.html", active="analytics")


@app.get("/admin/api/analytics")
@admin_required
def admin_api_analytics():
    return jsonify(admin_analytics(request.args.get("days", 30, type=int)))


def _csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


@app.get("/admin/export/users.csv")
@admin_required
def admin_export_users():
    import csv
    import io
    w = windows()
    Q = db.session.query
    tokens = RequestLog.tokens_in + RequestLog.tokens_out
    agg = {uid: (c, int(t), a) for uid, c, t, a in Q(
        RequestLog.user_id, func.count(RequestLog.id), func.coalesce(func.sum(tokens), 0), func.avg(RequestLog.latency_ms))
        .filter(RequestLog.status == "success", RequestLog.created_at >= w["month_start"]).group_by(RequestLog.user_id).all()}
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["name", "email", "plan", "status", "joined", "last_login", "requests_this_month",
                  "monthly_limit", "tokens_this_month", "avg_latency_ms"])
    for u in User.query.order_by(User.id).all():
        c, t, a = agg.get(u.id, (0, 0, None))
        out.writerow([_csv_safe(u.name), _csv_safe(u.email), u.plan, "active" if u.active else "disabled",
                      iso(u.created_at), iso(u.last_login) or "", c, plans_service.get_user_plan_dict(u)["monthly"], t,
                      int(a) if a else ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=upian-users.csv"})


# --- vision feature ---
from vision_routes import vision_bp
if "vision_bp" not in app.blueprints:
    app.register_blueprint(vision_bp)
# --- end vision feature ---


if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=False, threaded=True)
