from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

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
    password_hash = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.String(20), nullable=False, default="free")
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


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
