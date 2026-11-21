from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from config import PLANS, Config
from models import RequestLog, db, iso, utcnow


def local_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(Config.APP_TIMEZONE)
    except Exception:
        return timezone.utc


def windows(now_utc=None):
    """Start of the current local day / month (as naive UTC) and when they reset."""
    now_utc = now_utc or utcnow()
    tz = local_tz()
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    if month.month == 12:
        next_month = month.replace(year=month.year + 1, month=1)
    else:
        next_month = month.replace(month=month.month + 1)
    next_day = day + timedelta(days=1)

    def to_utc(d):
        return d.astimezone(timezone.utc).replace(tzinfo=None)

    return {
        "day_start": to_utc(day),
        "month_start": to_utc(month),
        "day_end": to_utc(next_day),
        "month_end": to_utc(next_month),
    }


def get_usage(user):
    w = windows()
    plan = PLANS.get(user.plan, PLANS["free"])

    def count(*conds):
        return (
            db.session.query(func.count(RequestLog.id))
            .filter(RequestLog.user_id == user.id, RequestLog.status == "success", *conds)
            .scalar()
            or 0
        )

    return {
        "plan": user.plan,
        "plan_label": plan["label"],
        "day": {"used": count(RequestLog.created_at >= w["day_start"]), "limit": plan["daily"]},
        "month": {"used": count(RequestLog.created_at >= w["month_start"]), "limit": plan["monthly"]},
        "strong": {
            "used": count(RequestLog.created_at >= w["month_start"], RequestLog.tier == "strong"),
            "limit": plan["strong_monthly"],
        },
        "images": {
            "used": count(RequestLog.created_at >= w["day_start"], RequestLog.classifier == "image-gen"),
            "limit": plan.get("image_daily", 0),
        },
        "day_resets_at": iso(w["day_end"]),
        "month_resets_at": iso(w["month_end"]),
    }
