"""Versioned Idempotent Database Migration v1.
Safely adds Google OAuth, plan status, subscription timestamps, and webhook idempotency.
Can be run safely multiple times without errors or data loss.
"""
import logging
import os
import sys

from sqlalchemy import inspect, text

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import Config
from models import db
from flask import Flask

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration.v1")


def run_migration(app=None):
    if app is None:
        app = Flask(__name__)
        app.config.from_object(Config)
        db.init_app(app)

    with app.app_context():
        engine = db.engine
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        is_sqlite = engine.dialect.name == "sqlite"

        logger.info("Running v1 migration on dialect: %s (tables: %s)", engine.dialect.name, table_names)

        # 1. Ensure basic tables exist
        db.create_all()

        # 2. Add columns to 'plans' table if missing
        if "plans" in table_names:
            p_columns = [c["name"] for c in inspector.get_columns("plans")]
            plan_col_defs = [
                ("price_yearly", "ALTER TABLE plans ADD COLUMN price_yearly FLOAT DEFAULT 0.0"),
                ("price_inr_yearly", "ALTER TABLE plans ADD COLUMN price_inr_yearly FLOAT DEFAULT 0.0"),
                ("is_default", "ALTER TABLE plans ADD COLUMN is_default BOOLEAN DEFAULT FALSE"),
            ]
            for col_name, sql in plan_col_defs:
                if col_name not in p_columns:
                    try:
                        with engine.begin() as conn:
                            logger.info("Adding column '%s' to plans", col_name)
                            conn.execute(text(sql))
                    except Exception as err:
                        logger.warning("Notice on adding '%s' to plans: %s", col_name, err)

        try:
            from plans_service import init_default_plans
            init_default_plans(app)
        except Exception as p_err:
            logger.warning("Default plans initialization note: %s", p_err)

        # 3. Add columns to 'users' table if missing
        if "users" in table_names:
            u_columns = [c["name"] for c in inspector.get_columns("users")]
            user_col_defs = [
                ("google_id", "ALTER TABLE users ADD COLUMN google_id VARCHAR(100)"),
                ("plan_status", "ALTER TABLE users ADD COLUMN plan_status VARCHAR(20) DEFAULT 'active'"),
                ("plan_start", "ALTER TABLE users ADD COLUMN plan_start TIMESTAMP"),
                ("plan_end", "ALTER TABLE users ADD COLUMN plan_end TIMESTAMP"),
                ("subscription_id", "ALTER TABLE users ADD COLUMN subscription_id VARCHAR(100)"),
                ("deleted_at", "ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP"),
            ]
            for col_name, sql in user_col_defs:
                if col_name not in u_columns:
                    try:
                        with engine.begin() as conn:
                            logger.info("Adding column '%s' to users", col_name)
                            conn.execute(text(sql))
                    except Exception as err:
                        logger.warning("Notice on adding '%s' to users: %s", col_name, err)

        # 4. Ensure webhook_events and audit_logs tables exist
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        if "webhook_events" not in table_names or "audit_logs" not in table_names:
            logger.info("Creating missing tables")
            db.create_all()

        logger.info("Migration v1 completed successfully.")
        return True


if __name__ == "__main__":
    run_migration()
