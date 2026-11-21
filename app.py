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
import re
import secrets
import threading
import time
import uuid
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
from config import PLAN_ORDER, PLANS, Config, env
import filetools
from file_routes import read_docs_text, register_files, save_images_pdf, save_export
from models import RequestLog, User, Payment, db, iso, utcnow
from quota import get_usage, local_tz, windows
from router import classifier

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)


# ----------------------------------------------------------------------------- setup
def seed_admin():
    email = Config.ADMIN_EMAIL.lower()
    if not email or not Config.ADMIN_PASSWORD:
        return
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(name=Config.ADMIN_NAME or "Admin", email=email, plan="plus_pro", is_admin=True)
        user.set_password(Config.ADMIN_PASSWORD)
        db.session.add(user)
        db.session.commit()
    elif not user.is_admin:
        user.is_admin = True
        db.session.commit()


with app.app_context():
    db.create_all()
    seed_admin()

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
        user = db.session.get(User, uid)
        if user and user.active:
            g.user = user
        else:
            session.pop("uid", None)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
        expected = session.get("csrf") or ""
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, description="Security token missing or expired. Reload the page and try again.")


@app.context_processor
def inject_globals():
    return dict(csrf_token=csrf_token, me=g.get("user"), PLANS=PLANS, tz_label=Config.APP_TIMEZONE)


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
                if not user.active:
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
            user = User(name=name, email=email, plan="free")
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
    boot = {
        "uid": g.user.id,
        "plan": g.user.plan,
        "can_image_gen": PLANS.get(g.user.plan, PLANS["free"]).get("image_gen", False),
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
    for p in PLAN_ORDER:
        p_info = PLANS[p]
        is_cur = (g.user.plan == p or (p == "plus" and g.user.plan == "pro") or (p == "plus_pro" and g.user.plan == "pro_plus"))
        plans_list.append({
            "id": p,
            "label": p_info["label"],
            "price": p_info.get("price", 0),
            "price_str": p_info.get("price_str", "$0"),
            "price_inr": p_info.get("price_inr", 0),
            "price_inr_str": p_info.get("price_inr_str", "₹0"),
            "period": p_info.get("period", "month"),
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
    for p in PLAN_ORDER:
        p_info = PLANS[p]
        is_cur = (g.user.plan == p or (p == "plus" and g.user.plan == "pro") or (p == "plus_pro" and g.user.plan == "pro_plus"))
        plans_list.append({
            "id": p,
            "label": p_info["label"],
            "price": p_info.get("price", 0),
            "price_str": p_info.get("price_str", "$0"),
            "price_inr": p_info.get("price_inr", 0),
            "price_inr_str": p_info.get("price_inr_str", "₹0"),
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
    plan_id = (data.get("plan") or "").strip().lower()
    if plan_id == "pro":
        plan_id = "plus"
    elif plan_id == "pro_plus":
        plan_id = "plus_pro"

    if plan_id not in PLANS or plan_id == "free":
        return jsonify(error="Invalid subscription plan selected.", code="invalid_plan"), 400

    plan_info = PLANS[plan_id]
    currency = getattr(Config, "PAYMENT_CURRENCY", "INR").upper()
    amount = float(plan_info.get("price_inr", 999) if currency == "INR" else plan_info.get("price", 15.0))
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
                "notes": {"user_id": g.user.id, "plan": plan_id, "email": g.user.email},
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
                    plan_label=plan_info["label"],
                    razorpay_enabled=True,
                )
        except Exception as e:
            app.logger.warning(f"Razorpay order creation fallback to simulated: {e}")

    # Fallback to simulated/test order when user hasn't created Razorpay account yet
    mock_order_id = "order_sim_" + uuid.uuid4().hex[:14]
    return jsonify(
        success=True,
        order_id=mock_order_id,
        key_id=razorpay_key or "rzp_test_simulated",
        amount=amount,
        currency=currency,
        plan=plan_id,
        plan_label=plan_info["label"],
        razorpay_enabled=False,
        simulated=True,
    )


@app.post("/api/payment/process")
@login_required
def api_payment_process():
    data = request.get_json(silent=True) or {}
    plan_id = (data.get("plan") or "").strip().lower()
    if plan_id == "pro":
        plan_id = "plus"
    elif plan_id == "pro_plus":
        plan_id = "plus_pro"

    if plan_id not in PLANS:
        return jsonify(error="Invalid subscription plan selected.", code="invalid_plan"), 400

    plan_info = PLANS[plan_id]
    if plan_id == "free":
        g.user.plan = "free"
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

    currency = getattr(Config, "PAYMENT_CURRENCY", "USD").upper()
    amount = float(plan_info.get("price_inr", 999) if currency == "INR" else plan_info.get("price", 15.0))

    payment = Payment(
        user_id=g.user.id,
        plan=plan_id,
        amount=amount,
        currency=currency,
        status="completed",
        transaction_id=tx_id,
        payment_method=payment_method,
    )
    db.session.add(payment)
    g.user.plan = plan_id
    db.session.commit()
    sync_upian_table(g.user)

    curr_symbol = "₹" if currency == "INR" else "$"
    return jsonify(
        success=True,
        transaction_id=tx_id,
        plan=plan_id,
        plan_label=plan_info["label"],
        amount=amount,
        currency=currency,
        message=f"Payment of {curr_symbol}{amount:.2f} successful! You are now upgraded to {plan_info['label']}.",
        usage=get_usage(g.user),
    )


@app.post("/api/payment/downgrade")
@login_required
def api_payment_downgrade():
    g.user.plan = "free"
    db.session.commit()
    sync_upian_table(g.user)
    return jsonify(
        success=True,
        message="Your account has been changed to the Free plan.",
        plan="free",
        plan_label="Free",
        usage=get_usage(g.user),
    )


register_files(app, login_required)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, classifier=classifier.status)


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


@app.post("/api/chat")
@login_required
def api_chat():
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify(error="Write a prompt first."), 400
    prompt = prompt.strip()
    if len(prompt) > Config.MAX_PROMPT_CHARS:
        return jsonify(error="Prompt is too long. The limit is %d characters." % Config.MAX_PROMPT_CHARS), 400

    user = g.user
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
                row = log(status="error", error=" | ".join(errors)[:1000], provider=chain[0]["provider"],
                          model=chain[0]["model"], slot=chain[0]["name"], fallback=len(chain) > 1)
                yield ndjson({"event": "error", "error": "The model did not respond. Try again in a moment. This request was not counted.",
                              "log": row.to_dict(), "usage": get_usage(user)})
                return

            fallback = bool(errors) or (tier == "strong" and used["name"] == "free")
            row = log(status="success", provider=used["provider"], model=used["model"], slot=used["name"],
                      fallback=fallback, tokens_in=result["tokens_in"], tokens_out=result["tokens_out"],
                      latency_ms=result["latency_ms"])
            yield ndjson({"event": "done", "reply": result["text"], "log": row.to_dict(), "usage": get_usage(user),
                          "files_used": files_used})
        except Exception:
            app.logger.exception("chat failed")
            db.session.rollback()
            yield ndjson({"event": "error", "error": "Something went wrong on our side. Try again."})

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
    query = User.query
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

    return render_template("admin_users.html", active="users", pag=pag, q=q, plan_order=PLAN_ORDER,
                           day_counts=counts(w["day_start"]), month_counts=counts(w["month_start"]))


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
        top.append({"name": u.name, "email": u.email, "plan": PLANS.get(u.plan, PLANS["free"])["label"],
                    "requests": d["requests"], "tokens": d["tokens"], "avg_latency_ms": _avg(d["lat"])})

    near = []
    for u in users.values():
        limit = PLANS.get(u.plan, PLANS["free"])["monthly"]
        used = month_counts.get(u.id, 0)
        if limit and used / limit >= 0.8:
            near.append({"name": u.name, "email": u.email, "plan": PLANS.get(u.plan, PLANS["free"])["label"],
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
                      iso(u.created_at), iso(u.last_login) or "", c, PLANS.get(u.plan, PLANS["free"])["monthly"], t,
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
