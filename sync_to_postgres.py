import os
import sys
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor

# Load environment variables
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SQLITE_PATH = BASE_DIR / "upian.db"
POSTGRES_URL = os.getenv("DATABASE_URL")

if POSTGRES_URL and POSTGRES_URL.startswith("postgres://"):
    POSTGRES_URL = POSTGRES_URL.replace("postgres://", "postgresql://", 1)


def get_pg_connection():
    if not POSTGRES_URL or not POSTGRES_URL.startswith("postgresql://"):
        print("[ERROR] DATABASE_URL is not set to a valid PostgreSQL URI in .env.")
        return None
    try:
        p = urlparse(POSTGRES_URL)
        conn = psycopg2.connect(
            dbname=p.path.lstrip("/"),
            user=p.username,
            password=p.password,
            host=p.hostname or "localhost",
            port=p.port or 5432,
            connect_timeout=5
        )
        return conn
    except Exception as exc:
        print(f"[ERROR] Could not connect to PostgreSQL: {exc}")
        return None


def sync():
    if not SQLITE_PATH.exists():
        print(f"[ERROR] SQLite database not found at {SQLITE_PATH}")
        return

    print("=" * 60)
    print("UP-IAN DATABASE SYNC: SQLite (upian.db) -> PostgreSQL")
    print("=" * 60)

    # 1. Connect to SQLite
    s_conn = sqlite3.connect(SQLITE_PATH)
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()

    # 2. Connect to PostgreSQL
    pg_conn = get_pg_connection()
    if not pg_conn:
        print("\n[HINT] Please make sure your PostgreSQL service is running on your machine.")
        return

    pg_cur = pg_conn.cursor()

    try:
        # First ensure tables exist by using SQLAlchemy metadata via app context
        try:
            from app import app, db
            with app.app_context():
                db.create_all()
        except Exception:
            pass

        # -------------------------------------------------------------
        # 2.5 Sync PLANS
        # -------------------------------------------------------------
        try:
            s_cur.execute("SELECT * FROM plans")
            s_plans = s_cur.fetchall()
            print(f"\n[Plans] Found {len(s_plans)} plans in SQLite. Syncing to PostgreSQL...")
            plans_synced = 0
            for pl in s_plans:
                pl_dict = dict(pl)
                code = pl_dict.get("code")
                pg_cur.execute("SELECT id FROM plans WHERE code = %s", (code,))
                existing_p = pg_cur.fetchone()
                if existing_p:
                    pg_cur.execute("""
                        UPDATE plans SET
                            label = %s,
                            price = %s,
                            price_inr = %s,
                            price_yearly = %s,
                            price_inr_yearly = %s,
                            period = %s,
                            description = %s,
                            tag = %s,
                            daily = %s,
                            monthly = %s,
                            strong_monthly = %s,
                            image_gen = %s,
                            image_daily = %s,
                            features_json = %s,
                            is_active = %s,
                            is_default = %s,
                            is_system = %s,
                            sort_order = %s
                        WHERE code = %s
                    """, (
                        pl_dict.get("label"),
                        pl_dict.get("price", 0.0),
                        pl_dict.get("price_inr", 0.0),
                        pl_dict.get("price_yearly", 0.0),
                        pl_dict.get("price_inr_yearly", 0.0),
                        pl_dict.get("period", "/mo"),
                        pl_dict.get("description", ""),
                        pl_dict.get("tag", ""),
                        pl_dict.get("daily", 100),
                        pl_dict.get("monthly", 2000),
                        pl_dict.get("strong_monthly", 400),
                        bool(pl_dict.get("image_gen", 0)),
                        pl_dict.get("image_daily", 0),
                        pl_dict.get("features_json", "[]"),
                        bool(pl_dict.get("is_active", 1)),
                        bool(pl_dict.get("is_default", 0)),
                        bool(pl_dict.get("is_system", 0)),
                        pl_dict.get("sort_order", 10),
                        code
                    ))
                else:
                    pg_cur.execute("""
                        INSERT INTO plans (
                            code, label, price, price_inr, price_yearly, price_inr_yearly,
                            period, description, tag, daily, monthly, strong_monthly,
                            image_gen, image_daily, features_json, is_active, is_default,
                            is_system, sort_order, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, NOW(), NOW()
                        )
                    """, (
                        code,
                        pl_dict.get("label"),
                        pl_dict.get("price", 0.0),
                        pl_dict.get("price_inr", 0.0),
                        pl_dict.get("price_yearly", 0.0),
                        pl_dict.get("price_inr_yearly", 0.0),
                        pl_dict.get("period", "/mo"),
                        pl_dict.get("description", ""),
                        pl_dict.get("tag", ""),
                        pl_dict.get("daily", 100),
                        pl_dict.get("monthly", 2000),
                        pl_dict.get("strong_monthly", 400),
                        bool(pl_dict.get("image_gen", 0)),
                        pl_dict.get("image_daily", 0),
                        pl_dict.get("features_json", "[]"),
                        bool(pl_dict.get("is_active", 1)),
                        bool(pl_dict.get("is_default", 0)),
                        bool(pl_dict.get("is_system", 0)),
                        pl_dict.get("sort_order", 10)
                    ))
                plans_synced += 1
            pg_conn.commit()
            print(f"      -> Plans: {plans_synced} plans synced.")
        except Exception as pl_err:
            print(f"      -> Plans notice: {pl_err}")

        # -------------------------------------------------------------
        # 3. Sync USERS
        # -------------------------------------------------------------
        s_cur.execute("SELECT * FROM users")
        s_users = s_cur.fetchall()
        print(f"\n[1/3] Found {len(s_users)} users in SQLite. Syncing to PostgreSQL...")

        users_added = 0
        users_updated = 0

        for u in s_users:
            u_dict = dict(u)
            email = u_dict.get("email")

            # Check if user exists in PostgreSQL
            pg_cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing = pg_cur.fetchone()

            if existing:
                # Update existing user record
                pg_cur.execute("""
                    UPDATE users SET
                        name = COALESCE(NULLIF(%s, ''), name),
                        plan = %s,
                        plan_status = %s,
                        plan_start = %s,
                        plan_end = %s,
                        google_id = COALESCE(%s, google_id),
                        last_login = COALESCE(%s, last_login),
                        is_admin = %s,
                        active = %s,
                        deleted_at = %s
                    WHERE email = %s
                """, (
                    u_dict.get("name"),
                    u_dict.get("plan", "free"),
                    u_dict.get("plan_status", "active"),
                    u_dict.get("plan_start"),
                    u_dict.get("plan_end"),
                    u_dict.get("google_id"),
                    u_dict.get("last_login"),
                    bool(u_dict.get("is_admin", 0)),
                    bool(u_dict.get("active", 1)),
                    u_dict.get("deleted_at"),
                    email
                ))
                users_updated += 1
            else:
                # Insert new user with same id if possible
                pg_cur.execute("""
                    INSERT INTO users (
                        id, name, email, password_hash, plan, plan_status,
                        plan_start, plan_end, subscription_id, google_id,
                        is_admin, active, created_at, last_login, deleted_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        email = EXCLUDED.email,
                        plan = EXCLUDED.plan,
                        google_id = COALESCE(EXCLUDED.google_id, users.google_id),
                        last_login = COALESCE(EXCLUDED.last_login, users.last_login),
                        deleted_at = EXCLUDED.deleted_at
                """, (
                    u_dict.get("id"),
                    u_dict.get("name", "User"),
                    email,
                    u_dict.get("password_hash", ""),
                    u_dict.get("plan", "free"),
                    u_dict.get("plan_status", "active"),
                    u_dict.get("plan_start"),
                    u_dict.get("plan_end"),
                    u_dict.get("subscription_id"),
                    u_dict.get("google_id"),
                    bool(u_dict.get("is_admin", 0)),
                    bool(u_dict.get("active", 1)),
                    u_dict.get("created_at"),
                    u_dict.get("last_login"),
                    u_dict.get("deleted_at")
                ))
                users_added += 1

        # Reset Postgres users ID sequence
        pg_cur.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE(MAX(id), 1)) FROM users;")
        pg_conn.commit()
        print(f"      -> Users: {users_added} inserted, {users_updated} updated.")

        # -------------------------------------------------------------
        # 4. Sync PAYMENTS
        # -------------------------------------------------------------
        try:
            s_cur.execute("SELECT * FROM payments")
            s_payments = s_cur.fetchall()
            print(f"\n[2/3] Found {len(s_payments)} payments in SQLite. Syncing to PostgreSQL...")
            payments_added = 0

            for p in s_payments:
                p_dict = dict(p)
                tx_id = p_dict.get("transaction_id")
                pg_cur.execute("SELECT id FROM payments WHERE transaction_id = %s", (tx_id,))
                if not pg_cur.fetchone():
                    pg_cur.execute("""
                        INSERT INTO payments (
                            user_id, plan, amount, currency, status,
                            transaction_id, payment_method, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        p_dict.get("user_id"),
                        p_dict.get("plan"),
                        p_dict.get("amount"),
                        p_dict.get("currency", "USD"),
                        p_dict.get("status", "completed"),
                        tx_id,
                        p_dict.get("payment_method", "card"),
                        p_dict.get("created_at")
                    ))
                    payments_added += 1

            pg_conn.commit()
            print(f"      -> Payments: {payments_added} new records inserted.")
        except Exception as p_err:
            print(f"      -> Payments notice: {p_err}")

        # -------------------------------------------------------------
        # 5. Sync REQUEST LOGS
        # -------------------------------------------------------------
        try:
            s_cur.execute("SELECT * FROM request_logs")
            s_logs = s_cur.fetchall()
            print(f"\n[3/3] Found {len(s_logs)} request logs in SQLite. Syncing to PostgreSQL...")
            logs_added = 0

            for l in s_logs:
                l_dict = dict(l)
                log_id = l_dict.get("id")
                pg_cur.execute("SELECT id FROM request_logs WHERE id = %s", (log_id,))
                if not pg_cur.fetchone():
                    pg_cur.execute("""
                        INSERT INTO request_logs (
                            id, user_id, created_at, prompt, tier, complexity,
                            classifier, provider, model, slot, fallback,
                            downgraded, tokens_in, tokens_out, latency_ms,
                            classify_ms, status
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        )
                    """, (
                        log_id,
                        l_dict.get("user_id"),
                        l_dict.get("created_at"),
                        l_dict.get("prompt", ""),
                        l_dict.get("tier", "weak"),
                        l_dict.get("complexity", 0.0),
                        l_dict.get("classifier", "heuristic"),
                        l_dict.get("provider", ""),
                        l_dict.get("model", ""),
                        l_dict.get("slot", ""),
                        bool(l_dict.get("fallback", 0)),
                        bool(l_dict.get("downgraded", 0)),
                        l_dict.get("tokens_in", 0),
                        l_dict.get("tokens_out", 0),
                        l_dict.get("latency_ms", 0),
                        l_dict.get("classify_ms", 0),
                        l_dict.get("status", "success")
                    ))
                    logs_added += 1

            pg_cur.execute("SELECT setval(pg_get_serial_sequence('request_logs', 'id'), COALESCE(MAX(id), 1)) FROM request_logs;")
            pg_conn.commit()
            print(f"      -> Request logs: {logs_added} new logs inserted.")
        except Exception as l_err:
            print(f"      -> Logs notice: {l_err}")

        print("\n" + "=" * 60)
        print("SYNC COMPLETED SUCCESSFULLY! All data is now in PostgreSQL.")
        print("=" * 60)

    except Exception as exc:
        pg_conn.rollback()
        print(f"\n[ERROR] Sync failed: {exc}")
    finally:
        s_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    sync()
