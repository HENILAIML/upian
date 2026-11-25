import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger("upian.models")
db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False, default="")
    plan = db.Column(db.String(20), nullable=False, default="free")
    plan_status = db.Column(db.String(20), nullable=False, default="active")
    plan_start = db.Column(db.DateTime, nullable=True, default=utcnow)
    plan_end = db.Column(db.DateTime, nullable=True)
    subscription_id = db.Column(db.String(100), nullable=True)
    google_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "plan": self.plan,
            "plan_status": self.plan_status,
            "plan_start": iso(self.plan_start),
            "plan_end": iso(self.plan_end),
            "subscription_id": self.subscription_id,
            "is_admin": self.is_admin,
            "active": self.active,
            "deleted_at": iso(self.deleted_at),
            "created_at": iso(self.created_at),
            "last_login": iso(self.last_login),
        }


class RequestLog(db.Model):
    __tablename__ = "request_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    prompt = db.Column(db.String(200), nullable=False, default="")
    tier = db.Column(db.String(10), nullable=False, default="weak")
    complexity = db.Column(db.Float, default=0.0)
    classifier = db.Column(db.String(20), default="heuristic")
    provider = db.Column(db.String(20), default="")
    model = db.Column(db.String(80), default="")
    slot = db.Column(db.String(20), default="")
    fallback = db.Column(db.Boolean, nullable=False, default=False)
    downgraded = db.Column(db.Boolean, nullable=False, default=False)
    tokens_in = db.Column(db.Integer, nullable=False, default=0)
    tokens_out = db.Column(db.Integer, nullable=False, default=0)
    latency_ms = db.Column(db.Integer, nullable=False, default=0)
    classify_ms = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(10), nullable=False, default="success", index=True)
    error = db.Column(db.Text)

    user = db.relationship("User", backref=db.backref("logs", lazy="dynamic"))

    __table_args__ = (db.Index("ix_logs_user_created", "user_id", "created_at"),)

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": iso(self.created_at),
            "prompt": self.prompt,
            "tier": self.tier,
            "complexity": self.complexity,
            "classifier": self.classifier,
            "provider": self.provider,
            "model": self.model,
            "slot": self.slot,
            "fallback": self.fallback,
            "downgraded": self.downgraded,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "classify_ms": self.classify_ms,
            "status": self.status,
            "error": self.error if self.status == "error" else None,
        }


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(80), nullable=False)
    ext = db.Column(db.String(12), nullable=False, default="")
    size = db.Column(db.Integer, nullable=False, default=0)
    file_hash = db.Column(db.String(64), nullable=False, index=True)
    chars = db.Column(db.Integer, nullable=False, default=0)
    pages = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self):
        return {"id": self.id, "filename": self.filename, "ext": self.ext, "size": self.size,
                "chars": self.chars, "pages": self.pages, "created_at": iso(self.created_at)}


class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(5), nullable=False, default="USD")
    status = db.Column(db.String(20), nullable=False, default="completed")
    transaction_id = db.Column(db.String(64), unique=True, nullable=False)
    payment_method = db.Column(db.String(30), default="card")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User", backref=db.backref("payments", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "plan": self.plan,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "transaction_id": self.transaction_id,
            "payment_method": self.payment_method,
            "created_at": iso(self.created_at),
        }


class WebhookEvent(db.Model):
    __tablename__ = "webhook_events"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(128), unique=True, nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    processed_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    status = db.Column(db.String(20), nullable=False, default="processed")

    def to_dict(self):
        return {
            "id": self.id,
            "event_id": self.event_id,
            "provider": self.provider,
            "processed_at": iso(self.processed_at),
            "status": self.status,
        }


def upsert_google_user(email, google_id, name=""):
    """
    Safely upserts a user following Google OAuth login.
    Guarantees no duplicate records are created, even under concurrent login requests.
    """
    clean_email = email.strip().lower()
    clean_google_id = str(google_id).strip()

    # Retry up to 3 times to handle concurrency/race conditions
    for attempt in range(3):
        try:
            # 1. Search by Google ID first
            user = User.query.filter_by(google_id=clean_google_id).first()
            if user:
                if name and not user.name:
                    user.name = name.strip()
                user.last_login = utcnow()
                db.session.commit()
                return user

            # 2. Search by email next (existing local account linking Google)
            user = User.query.filter_by(email=clean_email).first()
            if user:
                user.google_id = clean_google_id
                if name and (not user.name or user.name == "Admin"):
                    user.name = name.strip()
                user.last_login = utcnow()
                db.session.commit()
                return user

            # 3. Create new user if not found
            from plans_service import get_default_plan
            def_plan = get_default_plan()
            if not def_plan:
                logger.error("Signup failed: No default subscription plan configured in database.")
                raise RuntimeError("No default subscription plan is configured in the database. Please contact an administrator.")
            user = User(
                email=clean_email,
                name=name.strip() if name else clean_email.split("@")[0],
                google_id=clean_google_id,
                plan=def_plan.code,
                plan_status="active",
                plan_start=utcnow(),
                last_login=utcnow(),
            )
            # Empty password hash prevents local password logins until explicitly set
            user.password_hash = ""
            db.session.add(user)
            db.session.commit()
            return user
        except Exception as exc:
            db.session.rollback()
            logger.warning("Concurrency retry in upsert_google_user attempt %d: %s", attempt + 1, exc)
            if attempt == 2:
                # Final fallback check
                user = User.query.filter((User.google_id == clean_google_id) | (User.email == clean_email)).first()
                if user:
                    return user
                raise exc


def update_user_plan_idempotent(user_id, plan, transaction_id, amount, currency="USD", payment_method="card", duration_days=30, webhook_event_id=None):
    """
    Updates a user's subscription plan inside an atomic database transaction.
    Guarantees strict idempotency: repeated calls with the same transaction_id or webhook_event_id
    will not double-apply or duplicate payment records.
    """
    if webhook_event_id:
        existing_event = WebhookEvent.query.filter_by(event_id=webhook_event_id).first()
        if existing_event:
            logger.info("Webhook event %s already processed. Skipping duplicate.", webhook_event_id)
            user = db.session.get(User, user_id)
            return True, None, "Webhook already processed"

    # Check for existing completed payment with this transaction_id
    existing_payment = Payment.query.filter_by(transaction_id=transaction_id).first()
    if existing_payment:
        logger.info("Transaction %s already exists. Returning idempotent success.", transaction_id)
        return True, existing_payment, "Transaction already processed"

    user = db.session.get(User, user_id)
    if not user:
        return False, None, "User not found"

    now = utcnow()
    try:
        # Atomic update in transaction
        user.plan = plan
        user.plan_status = "active"
        user.plan_start = now
        user.plan_end = now + timedelta(days=duration_days)

        payment = Payment(
            user_id=user.id,
            plan=plan,
            amount=float(amount),
            currency=currency.upper(),
            status="completed",
            transaction_id=transaction_id,
            payment_method=payment_method,
            created_at=now,
        )
        db.session.add(payment)

        if webhook_event_id:
            event = WebhookEvent(
                event_id=webhook_event_id,
                provider="payment_webhook",
                payload_hash=hashlib.sha256(f"{transaction_id}:{user_id}:{amount}".encode()).hexdigest(),
                status="processed",
            )
            db.session.add(event)

        db.session.commit()
        return True, payment, "Plan updated successfully"
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to update user plan atomically: %s", exc)
        raise exc


def verify_webhook_signature(payload_bytes, signature_header, secret):
    """
    Verifies HMAC-SHA256 signature for incoming payment webhooks.
    """
    if not secret or not signature_header or not payload_bytes:
        return False
    expected = hmac.new(
        secret.encode("utf-8") if isinstance(secret, str) else secret,
        payload_bytes if isinstance(payload_bytes, bytes) else payload_bytes.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    # Support both bare hex and 'sha256=...' prefix
    clean_sig = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, clean_sig)


class Plan(db.Model):
    __tablename__ = "plans"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    label = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    price_inr = db.Column(db.Float, nullable=False, default=0.0)
    price_yearly = db.Column(db.Float, nullable=False, default=0.0)
    price_inr_yearly = db.Column(db.Float, nullable=False, default=0.0)
    period = db.Column(db.String(30), nullable=False, default="/month")
    description = db.Column(db.String(255), nullable=False, default="")
    tag = db.Column(db.String(50), nullable=True, default="")
    daily = db.Column(db.Integer, nullable=False, default=100)
    monthly = db.Column(db.Integer, nullable=False, default=2000)
    strong_monthly = db.Column(db.Integer, nullable=False, default=400)
    image_gen = db.Column(db.Boolean, nullable=False, default=False)
    image_daily = db.Column(db.Integer, nullable=False, default=0)
    features_json = db.Column(db.Text, nullable=True, default="[]")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_system = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=10)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def get_features(self):
        try:
            import json
            return json.loads(self.features_json or "[]")
        except Exception:
            return []

    def set_features(self, feat_list):
        import json
        if isinstance(feat_list, list):
            self.features_json = json.dumps(feat_list)
        elif isinstance(feat_list, str):
            lines = [l.strip().lstrip("•-*✔ ").strip() for l in feat_list.split("\n") if l.strip()]
            self.features_json = json.dumps(lines)
        else:
            self.features_json = json.dumps([])

    def to_dict(self):
        feats = self.get_features()
        price_inr_val = float(self.price_inr or 0)
        price_val = float(self.price or 0)
        price_inr_yr_val = float(self.price_inr_yearly or 0)
        if price_inr_yr_val <= 0 and price_inr_val > 0:
            price_inr_yr_val = round(price_inr_val * 10, 2)
        price_yr_val = float(self.price_yearly or 0)
        if price_yr_val <= 0 and price_val > 0:
            price_yr_val = round(price_val * 10, 2)

        return {
            "id": self.id,
            "code": self.code,
            "label": self.label,
            "price": price_val,
            "price_str": f"${int(price_val)}" if price_val.is_integer() else f"${price_val:.2f}",
            "price_inr": price_inr_val,
            "price_inr_str": f"₹{int(price_inr_val)}" if price_inr_val.is_integer() else f"₹{price_inr_val:.2f}",
            "price_yearly": price_yr_val,
            "price_yearly_str": f"${int(price_yr_val)}" if price_yr_val.is_integer() else f"${price_yr_val:.2f}",
            "price_inr_yearly": price_inr_yr_val,
            "price_inr_yearly_str": f"₹{int(price_inr_yr_val)}" if price_inr_yr_val.is_integer() else f"₹{price_inr_yr_val:.2f}",
            "period": self.period,
            "description": self.description,
            "tag": self.tag or "",
            "daily": self.daily,
            "monthly": self.monthly,
            "strong_monthly": self.strong_monthly,
            "image_gen": self.image_gen,
            "image_daily": self.image_daily,
            "features": feats,
            "is_active": self.is_active,
            "is_default": bool(self.is_default),
            "is_system": self.is_system,
            "sort_order": self.sort_order,
            "created_at": iso(self.created_at),
        }

    def to_plan_dict(self):
        feats = self.get_features()
        price_inr_val = float(self.price_inr or 0)
        price_val = float(self.price or 0)
        price_inr_yr_val = float(self.price_inr_yearly or 0)
        if price_inr_yr_val <= 0 and price_inr_val > 0:
            price_inr_yr_val = round(price_inr_val * 10, 2)
        price_yr_val = float(self.price_yearly or 0)
        if price_yr_val <= 0 and price_val > 0:
            price_yr_val = round(price_val * 10, 2)

        return {
            "id": self.code,
            "label": self.label,
            "price": price_val,
            "price_str": f"${int(price_val)}" if price_val.is_integer() else f"${price_val:.2f}",
            "price_inr": price_inr_val,
            "price_inr_str": f"₹{int(price_inr_val)}" if price_inr_val.is_integer() else f"₹{price_inr_val:.2f}",
            "price_yearly": price_yr_val,
            "price_yearly_str": f"${int(price_yr_val)}" if price_yr_val.is_integer() else f"${price_yr_val:.2f}",
            "price_inr_yearly": price_inr_yr_val,
            "price_inr_yearly_str": f"₹{int(price_inr_yr_val)}" if price_inr_yr_val.is_integer() else f"₹{price_inr_yr_val:.2f}",
            "period": self.period,
            "description": self.description,
            "tag": self.tag or "",
            "daily": self.daily,
            "monthly": self.monthly,
            "strong_monthly": self.strong_monthly,
            "image_gen": self.image_gen,
            "image_daily": self.image_daily,
            "features": feats,
            "is_default": bool(self.is_default),
            "is_system": self.is_system,
        }


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    target_user_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "admin_id": self.admin_id,
            "target_user_id": self.target_user_id,
            "action": self.action,
            "details": self.details,
            "created_at": iso(self.created_at),
        }
