"""Integration Tests for Database Backup and Restore Automation (Task 5)."""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from app import app
from models import Document, Payment, RequestLog, User, db
from scripts.backup import create_backup
from scripts.restore import restore_backup


@pytest.fixture
def seeded_db():
    with app.app_context():
        # Ensure test data exists
        email = "backup_test_user@upian.local"
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                name="Backup Test User",
                email=email,
                plan="plus",
                plan_status="active",
                google_id="google_test_12345",
            )
            user.set_password("SecurePass@123")
            db.session.add(user)
            db.session.commit()

        # Add payment
        tx_id = "TX-BACKUP-TEST-99"
        if not Payment.query.filter_by(transaction_id=tx_id).first():
            pay = Payment(
                user_id=user.id,
                plan="plus",
                amount=15.0,
                currency="USD",
                transaction_id=tx_id,
            )
            db.session.add(pay)
            db.session.commit()

        yield user


def test_backup_creation_and_retention(seeded_db, tmp_path):
    backup_dir = tmp_path / "test_backups"

    # Create 5 backups with keep_n = 3
    res_list = []
    import time
    for _ in range(5):
        time.sleep(0.02)
        res = create_backup(backup_dir=backup_dir, keep_n=3)
        assert res["status"] == "success"
        assert res["size_bytes"] > 0
        assert os.path.exists(res["backup_path"])
        res_list.append(res)

    # Verify retention policy: only the 3 newest backup files exist in directory
    existing_backups = [f for f in backup_dir.iterdir() if f.name.endswith((".dump", ".db", ".sqlite"))]
    assert len(existing_backups) == 3

    # Verify backup log exists and recorded entries
    log_file = backup_dir / "backup_log.jsonl"
    assert log_file.exists()
    with open(log_file, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 5


def test_restore_into_scratch_database_and_verify_row_counts(seeded_db, tmp_path):
    backup_dir = tmp_path / "scratch_backups"
    scratch_db_file = tmp_path / "scratch_restored.db"
    scratch_db_uri = f"sqlite:///{scratch_db_file}"

    # 1. Count original rows in current database
    with app.app_context():
        orig_users = User.query.count()
        orig_payments = Payment.query.count()
        orig_logs = RequestLog.query.count()

    assert orig_users > 0

    # 2. Run backup
    backup_meta = create_backup(backup_dir=backup_dir, keep_n=2)
    assert backup_meta["status"] == "success"
    backup_file = Path(backup_meta["backup_path"])
    assert backup_file.exists()

    # 3. Restore backup into scratch database
    restore_res = restore_backup(backup_file=backup_file, target_db_uri=scratch_db_uri)
    assert restore_res["status"] == "success"
    assert os.path.exists(scratch_db_file)

    # 4. Count restored rows in scratch database
    scratch_counts = restore_res["row_counts"]
    assert "users" in scratch_counts
    assert "payments" in scratch_counts

    assert scratch_counts["users"] == orig_users, (
        f"Row count mismatch in users: original {orig_users} vs scratch {scratch_counts['users']}"
    )
    assert scratch_counts["payments"] == orig_payments, (
        f"Row count mismatch in payments: original {orig_payments} vs scratch {scratch_counts['payments']}"
    )
