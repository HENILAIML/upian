"""Dynamic Plans Management Service.
Handles database persistence, synchronization to memory, and admin CRUD for subscription plans.
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from config import Config, PLANS, PLAN_ORDER
from models import AuditLog, Plan, User, db

logger = logging.getLogger("upian.plans_service")


def init_default_plans(app=None) -> None:
    """Seeds default baseline plans if the database table is empty, ensuring default plan is configured."""
    try:
        # Check if plans exist
        existing_count = Plan.query.count()
        if existing_count == 0:
            logger.info("Seeding default baseline plans into database...")
            default_seed = [
                Plan(
                    code="free",
                    label="Free",
                    price=0.0,
                    price_inr=0.0,
                    price_yearly=0.0,
                    price_inr_yearly=0.0,
                    period="forever",
                    description="Essential multimodal gateway with daily community quota.",
                    tag="STARTER",
                    daily=20,
                    monthly=300,
                    strong_monthly=40,
                    image_gen=False,
                    image_daily=0,
                    features_json='["20 requests per day", "300 requests per month", "40 complex reasoning calls", "Multimodal chat & files", "No AI Image Generation"]',
                    is_active=True,
                    is_default=True,
                    is_system=False,
                    sort_order=1,
                ),
                Plan(
                    code="plus",
                    label="Pro",
                    price=15.0,
                    price_inr=999.0,
                    price_yearly=150.0,
                    price_inr_yearly=9999.0,
                    period="/mo",
                    description="For power users who need high daily limits and AI image generation.",
                    tag="RECOMMENDED",
                    daily=100,
                    monthly=2000,
                    strong_monthly=400,
                    image_gen=True,
                    image_daily=20,
                    features_json='["100 daily requests with priority speeds", "20 AI Images per day (Imagen 3 & DALL-E)", "Deep Reasoning (Qwen3 + Gemini 3.8 Flash)", "Document exports (.xlsx, .pdf, .docx)"]',
                    is_active=True,
                    is_default=False,
                    is_system=False,
                    sort_order=2,
                ),
                Plan(
                    code="plus_pro",
                    label="Pro Plus",
                    price=39.0,
                    price_inr=2499.0,
                    price_yearly=390.0,
                    price_inr_yearly=24999.0,
                    period="/mo",
                    description="Maximum limits and dedicated access to top-tier models.",
                    tag="MAX POWER",
                    daily=300,
                    monthly=6000,
                    strong_monthly=2000,
                    image_gen=True,
                    image_daily=30,
                    features_json='["Unlimited daily requests", "30 AI Images per day (Ultra-HD Quality)", "Gemini 3.8 Pro + Complex Reasoning", "Priority 24/7 support & early beta access"]',
                    is_active=True,
                    is_default=False,
                    is_system=False,
                    sort_order=3,
                ),
            ]

            db.session.bulk_save_objects(default_seed)
            db.session.commit()
            logger.info("Successfully seeded default plans.")
        else:
            # Idempotent verification: Ensure at least one active plan has is_default = True
            default_p = Plan.query.filter_by(is_active=True, is_default=True).first()
            if not default_p:
                # Find zero-price plan or first active plan
                candidate = Plan.query.filter(Plan.is_active == True, Plan.price_inr <= 0).first()
                if not candidate:
                    candidate = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order.asc()).first()
                if not candidate:
                    candidate = Plan.query.filter(Plan.price_inr <= 0).first() or Plan.query.order_by(Plan.sort_order.asc()).first()
                if candidate:
                    candidate.is_default = True
                    candidate.is_active = True
                    db.session.commit()

            # Ensure baseline plans are active and editable
            Plan.query.filter(Plan.code.in_(["free", "pro", "plus", "plus_pro"])).update({"is_active": True})
            free_p = Plan.query.filter_by(code="free").first()
            if free_p and free_p.is_system:
                free_p.is_system = False
            db.session.commit()

        sync_plans_to_memory()
    except Exception as exc:
        db.session.rollback()
        logger.warning("Default plans initialization note: %s", exc)


ensure_seed_plans = init_default_plans


def sync_plans_to_memory() -> None:
    """Loads all active plans from database and updates config.PLANS and config.PLAN_ORDER in-place."""
    try:
        active_plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order.asc(), Plan.id.asc()).all()
        if not active_plans:
            return

        order_codes = []
        for p in active_plans:
            if p.code == "custom":
                continue
            p_dict = p.to_plan_dict()
            PLANS[p.code] = p_dict
            order_codes.append(p.code)
            # Aliases for compatibility
            lbl_lower = (p.label or "").strip().lower()
            if p.code == "plus" or lbl_lower == "pro":
                PLANS["pro"] = p_dict
                PLANS["plus"] = p_dict
            elif p.code == "plus_pro" or lbl_lower in ("pro plus", "pro_plus"):
                PLANS["pro_plus"] = p_dict
                PLANS["plus_pro"] = p_dict
            elif p.code == "free" or lbl_lower == "free":
                PLANS["free"] = p_dict

        # Update PLAN_ORDER in-place so all references keep working
        PLAN_ORDER.clear()
        PLAN_ORDER.extend(order_codes)
    except Exception as exc:
        logger.warning("sync_plans_to_memory warning: %s", exc)


def get_default_plan() -> Optional[Plan]:
    """Returns the default plan for new signups and fallback quotas."""
    try:
        p = Plan.query.filter_by(is_active=True, is_default=True).first()
        if p:
            return p
        p = Plan.query.filter(Plan.is_active == True, Plan.price_inr <= 0).order_by(Plan.sort_order.asc()).first()
        if p:
            return p
        return None
    except Exception as exc:
        logger.warning("get_default_plan error: %s", exc)
        return None


def get_user_plan_dict(user) -> dict:
    """Dynamically resolves a user's subscription plan dictionary from database or sync cache."""
    user_plan_code = getattr(user, "plan", None) if user else None
    if user_plan_code and user_plan_code in PLANS:
        return PLANS[user_plan_code]

    if user_plan_code:
        try:
            db_plan = Plan.query.filter_by(code=user_plan_code).first()
            if db_plan:
                p_dict = db_plan.to_plan_dict()
                PLANS[db_plan.code] = p_dict
                return p_dict
        except Exception:
            pass

    def_plan = get_default_plan()
    if def_plan:
        p_dict = def_plan.to_plan_dict()
        PLANS[def_plan.code] = p_dict
        return p_dict

    return {
        "id": user_plan_code or "free",
        "label": (user_plan_code or "Free").capitalize(),
        "daily": 20,
        "monthly": 300,
        "strong_monthly": 40,
        "image_gen": False,
        "image_daily": 0,
        "features": [],
        "is_default": True,
    }


def get_all_plans_for_admin() -> List[Plan]:
    """Returns all plans (including inactive) for admin management."""
    return Plan.query.order_by(Plan.sort_order.asc(), Plan.id.asc()).all()


def set_plan_as_default(plan_id: int, admin_id: Optional[int] = None) -> Tuple[bool, str]:
    """Sets a plan as the system default plan, clearing default flag on other plans."""
    target = db.session.get(Plan, plan_id)
    if not target:
        return False, "Plan not found."
    if not target.is_active:
        return False, "Cannot set an inactive plan as default."

    try:
        # Clear default flag on all plans
        Plan.query.update({"is_default": False})
        target.is_default = True

        audit = AuditLog(
            admin_id=admin_id,
            action="set_default_plan",
            details=json.dumps({"plan_id": plan_id, "code": target.code, "label": target.label}),
        )
        db.session.add(audit)
        db.session.commit()

        sync_plans_to_memory()
        return True, f"Plan '{target.label}' is now the default plan for new users."
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to set default plan: %s", exc)
        return False, f"Database error: {str(exc)}"


def create_new_plan(
    label: str,
    price_inr: float,
    code: Optional[str] = None,
    price_usd: Optional[float] = None,
    price_inr_yearly: Optional[float] = None,
    price_usd_yearly: Optional[float] = None,
    period: str = "/mo",
    description: str = "",
    tag: str = "",
    daily: int = 100,
    monthly: int = 2000,
    strong_monthly: int = 400,
    image_gen: bool = False,
    image_daily: int = 0,
    features: Optional[List[str]] = None,
    is_default: bool = False,
    admin_id: Optional[int] = None,
) -> Tuple[bool, Optional[Plan], str]:
    """Creates a new plan with manual monthly/yearly price and quotas."""
    label = (label or "").strip()
    if not label:
        return False, None, "Plan name/label is required."

    if not code or not code.strip():
        clean_code = re.sub(r"[^a-zA-Z0-9_]+", "_", label.lower()).strip("_")
    else:
        clean_code = re.sub(r"[^a-zA-Z0-9_]+", "_", code.lower()).strip("_")

    if not clean_code:
        clean_code = f"plan_{int(price_inr)}"

    if Plan.query.filter_by(code=clean_code).first():
        return False, None, f"A plan with code '{clean_code}' already exists."

    price_inr_val = max(0.0, float(price_inr or 0.0))
    if price_usd is None or price_usd == "":
        price_usd_val = round(price_inr_val / 83.0, 2)
    else:
        price_usd_val = max(0.0, float(price_usd))

    if price_inr_yearly is None or price_inr_yearly == "":
        price_inr_yr_val = round(price_inr_val * 10, 2)
    else:
        price_inr_yr_val = max(0.0, float(price_inr_yearly))

    if price_usd_yearly is None or price_usd_yearly == "":
        price_usd_yr_val = round(price_inr_yr_val / 83.0, 2) if price_inr_yr_val > 0 else round(price_usd_val * 10, 2)
    else:
        price_usd_yr_val = max(0.0, float(price_usd_yearly))

    max_order = db.session.query(db.func.max(Plan.sort_order)).scalar() or 10

    if is_default:
        Plan.query.update({"is_default": False})

    new_plan = Plan(
        code=clean_code,
        label=label,
        price=price_usd_val,
        price_inr=price_inr_val,
        price_yearly=price_usd_yr_val,
        price_inr_yearly=price_inr_yr_val,
        period=(period or "/mo").strip(),
        description=(description or "").strip(),
        tag=(tag or "").strip(),
        daily=max(0, int(daily or 100)),
        monthly=max(0, int(monthly or 2000)),
        strong_monthly=max(0, int(strong_monthly or 400)),
        image_gen=bool(image_gen),
        image_daily=max(0, int(image_daily or 0)),
        is_active=True,
        is_default=bool(is_default),
        is_system=False,
        sort_order=max_order + 1,
    )
    if features:
        new_plan.set_features(features)

    try:
        db.session.add(new_plan)
        db.session.flush()

        audit = AuditLog(
            admin_id=admin_id,
            action="create_plan",
            details=json.dumps({"plan_id": new_plan.id, "code": clean_code, "label": label, "price_inr": price_inr_val}),
        )
        db.session.add(audit)
        db.session.commit()
        sync_plans_to_memory()
        return True, new_plan, "Plan created successfully."
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to create plan: %s", exc)
        return False, None, f"Database error: {str(exc)}"


def update_existing_plan(
    plan_id: int,
    label: str,
    price_inr: float,
    price_usd: Optional[float] = None,
    price_inr_yearly: Optional[float] = None,
    price_usd_yearly: Optional[float] = None,
    period: str = "/mo",
    description: str = "",
    tag: str = "",
    daily: int = 100,
    monthly: int = 2000,
    strong_monthly: int = 400,
    image_gen: bool = False,
    image_daily: int = 0,
    features: Optional[List[str]] = None,
    is_active: bool = True,
    is_default: Optional[bool] = None,
    admin_id: Optional[int] = None,
) -> Tuple[bool, Optional[Plan], str]:
    """Updates an existing plan's monthly & yearly price, quotas, and metadata."""
    plan = db.session.get(Plan, plan_id)
    if not plan:
        return False, None, "Plan not found."

    label = (label or "").strip()
    if not label:
        return False, None, "Plan name/label is required."

    price_inr_val = max(0.0, float(price_inr or 0.0))
    if price_usd is None or price_usd == "":
        price_usd_val = round(price_inr_val / 83.0, 2)
    else:
        price_usd_val = max(0.0, float(price_usd))

    if price_inr_yearly is None or price_inr_yearly == "":
        price_inr_yr_val = round(price_inr_val * 10, 2)
    else:
        price_inr_yr_val = max(0.0, float(price_inr_yearly))

    if price_usd_yearly is None or price_usd_yearly == "":
        price_usd_yr_val = round(price_inr_yr_val / 83.0, 2) if price_inr_yr_val > 0 else round(price_usd_val * 10, 2)
    else:
        price_usd_yr_val = max(0.0, float(price_usd_yearly))

    plan.label = label
    plan.price_inr = price_inr_val
    plan.price = price_usd_val
    plan.price_inr_yearly = price_inr_yr_val
    plan.price_yearly = price_usd_yr_val
    plan.period = (period or "/mo").strip()
    plan.description = (description or "").strip()
    plan.tag = (tag or "").strip()
    plan.daily = max(0, int(daily or 100))
    plan.monthly = max(0, int(monthly or 2000))
    plan.strong_monthly = max(0, int(strong_monthly or 400))
    plan.image_gen = bool(image_gen)
    plan.image_daily = max(0, int(image_daily or 0))
    plan.is_active = bool(is_active)

    if is_default is not None and is_default:
        Plan.query.update({"is_default": False})
        plan.is_default = True

    if features is not None:
        plan.set_features(features)

    try:
        audit = AuditLog(
            admin_id=admin_id,
            action="update_plan",
            details=json.dumps({
                "plan_id": plan.id,
                "code": plan.code,
                "label": label,
                "price_inr": price_inr_val,
                "daily": plan.daily,
                "monthly": plan.monthly,
                "strong_monthly": plan.strong_monthly,
            }),
        )
        db.session.add(audit)
        db.session.commit()
        sync_plans_to_memory()
        return True, plan, "Plan updated successfully."
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to update plan %s: %s", plan_id, exc)
        return False, None, f"Database error: {str(exc)}"


def remove_plan(plan_id: int, replacement_plan_id: Optional[int] = None, admin_id: Optional[int] = None) -> Tuple[bool, str]:
    """
    Deletes a plan.
    - If the plan is the last remaining default plan, blocks deletion.
    - If the plan has assigned users, reassigns them to replacement_plan_id in one atomic transaction.
    - Writes an audit log entry.
    """
    plan = db.session.get(Plan, plan_id)
    if not plan:
        return False, "Plan not found."

    # 1. Block deleting the last default plan
    if plan.is_default:
        other_defaults = Plan.query.filter(Plan.id != plan_id, Plan.is_active == True, Plan.is_default == True).count()
        if other_defaults == 0:
            return False, "Cannot delete the default plan because no other plan is set as default. Set another plan as default first."

    # 2. Check if users are assigned to this plan
    users_count = User.query.filter_by(plan=plan.code).count()
    replacement_plan = None
    if users_count > 0:
        if not replacement_plan_id:
            return False, f"Plan '{plan.label}' has {users_count} user(s). You must select a replacement plan to migrate them to."
        if int(replacement_plan_id) == plan_id:
            return False, "Replacement plan cannot be the plan being deleted."
        replacement_plan = db.session.get(Plan, int(replacement_plan_id))
        if not replacement_plan:
            return False, "Selected replacement plan does not exist."

    label = plan.label
    code_to_remove = plan.code

    try:
        migrated_users = 0
        if users_count > 0 and replacement_plan:
            migrated_users = User.query.filter_by(plan=code_to_remove).update({"plan": replacement_plan.code})

        db.session.delete(plan)

        audit = AuditLog(
            admin_id=admin_id,
            action="delete_plan",
            details=json.dumps({
                "plan_id": plan_id,
                "code": code_to_remove,
                "label": label,
                "users_migrated": migrated_users,
                "replacement_plan_code": replacement_plan.code if replacement_plan else None,
            }),
        )
        db.session.add(audit)
        db.session.commit()

        PLANS.pop(code_to_remove, None)
        if code_to_remove in PLAN_ORDER:
            PLAN_ORDER.remove(code_to_remove)
        sync_plans_to_memory()

        msg = f"Plan '{label}' was successfully removed."
        if migrated_users > 0:
            msg += f" {migrated_users} user(s) were reassigned to '{replacement_plan.label}'."
        return True, msg
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to remove plan %s: %s", plan_id, exc)
        return False, f"Error removing plan: {str(exc)}"
