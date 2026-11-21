import hmac
import json
import re
import secrets
import threading
import time
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
from config import PLAN_ORDER, PLANS, Config
import filetools
from file_routes import read_docs_text, register_files
from models import RequestLog, User, db, iso, utcnow
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
        "usage": get_usage(g.user),
        "threshold": Config.CLASSIFIER_THRESHOLD,
    }
    return render_template("chat.html", boot=boot, max_chars=Config.MAX_PROMPT_CHARS)


@app.get("/activity")
@login_required
def activity():
    return render_template("activity.html", boot={"threshold": Config.CLASSIFIER_THRESHOLD, "usage": get_usage(g.user)})


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

    # Attached files: add the text of the chosen files (only the user's own) to the question
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
            # Real-data answers (gold/silver prices) and file exports (excel, pdf, word...) skip the AI model.
            started = time.perf_counter()
            try:
                sp = special.handle(user.id, prompt, raw_history) if not files_used else None
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
                              "files_used": [], "file": sp["file"]})
                return

            route = classifier.classify(prompt)
            tier, downgraded = route["tier"], False
            if tier == "strong" and usage["strong"]["used"] >= usage["strong"]["limit"]:
                tier, downgraded = "weak", True
            chain = llm.build_chain(tier)

            def log(**extra):
                row = RequestLog(
                    user_id=user.id, prompt=prompt[:200], tier=tier, complexity=route["score"],
                    classifier=route["method"], classify_ms=route["classify_ms"],
                    downgraded=downgraded, **extra)
                db.session.add(row)
                db.session.commit()
                return row

            if not chain:
                row = log(status="error", error="No API key is configured on the server.")
                yield ndjson({"event": "error", "error": "No model is configured on the server yet. Ask the admin to add an API key.",
                              "log": row.to_dict(), "usage": get_usage(user)})
                return

            yield ndjson({"event": "route", "tier": tier, "score": route["score"], "classifier": route["method"],
                          "classify_ms": route["classify_ms"], "downgraded": downgraded,
                          "model": chain[0]["model"], "provider": chain[0]["provider"]})

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


if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=False, threaded=True)
