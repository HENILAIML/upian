"""Database Backup Automation Script (Task 5).

Generates timestamped database backups (supports PostgreSQL via pg_dump and SQLite fallback).
Implements retention policy keeping the last N backups and logs every run.
"""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import BASE_DIR, Config, env, env_int

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backup")

DEFAULT_BACKUP_DIR = Path(BASE_DIR) / "backups"
DEFAULT_KEEP_N = env_int("BACKUP_KEEP_N", 5)


def create_backup(backup_dir: Path = DEFAULT_BACKUP_DIR, keep_n: int = DEFAULT_KEEP_N, db_uri: str = None) -> dict:
    """
    Executes a timestamped database backup and prunes backups older than keep_n.
    Returns metadata dict of the backup run.
    """
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_uri = db_uri or Config.SQLALCHEMY_DATABASE_URI
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    is_postgres = db_uri.startswith("postgresql://") or db_uri.startswith("postgres://")

    status = "failed"
    backup_path = None
    size_bytes = 0
    error_msg = None

    started = datetime.now(timezone.utc)

    try:
        if is_postgres:
            parsed = urlparse(db_uri)
            backup_file = backup_dir / f"pg_backup_{timestamp}.dump"
            cmd = [
                "pg_dump",
                "-h", parsed.hostname or "localhost",
                "-p", str(parsed.port or 5432),
                "-U", parsed.username or "postgres",
                "-F", "c",
                "-f", str(backup_file),
                parsed.path.lstrip("/"),
            ]
            env_vars = os.environ.copy()
            if parsed.password:
                env_vars["PGPASSWORD"] = parsed.password

            # Run pg_dump
            proc = subprocess.run(cmd, env=env_vars, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(f"pg_dump exited with code {proc.returncode}: {proc.stderr}")
            backup_path = backup_file
        else:
            # SQLite backup
            sqlite_file = db_uri.replace("sqlite:///", "")
            if not os.path.exists(sqlite_file):
                # Touch empty sqlite if not exists
                import sqlite3
                conn = sqlite3.connect(sqlite_file)
                conn.close()
            backup_file = backup_dir / f"sqlite_backup_{timestamp}.db"
            shutil.copy2(sqlite_file, backup_file)
            backup_path = backup_file

        size_bytes = os.path.getsize(backup_path)
        status = "success"
        logger.info("Backup successfully created at %s (%d bytes)", backup_path, size_bytes)

    except Exception as exc:
        error_msg = str(exc)
        logger.error("Backup failed: %s", exc)

    # Apply Retention Policy: Keep last N backups
    pruned = []
    try:
        pattern = "pg_backup_*.dump" if is_postgres else "sqlite_backup_*.db"
        existing = sorted(backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
        if len(existing) > keep_n:
            to_remove = existing[:-keep_n]
            for old_file in to_remove:
                try:
                    old_file.unlink()
                    pruned.append(str(old_file))
                    logger.info("Pruned old backup file: %s", old_file)
                except Exception as p_err:
                    logger.warning("Could not prune %s: %s", old_file, p_err)
    except Exception as ret_err:
        logger.warning("Retention cleanup error: %s", ret_err)

    # Log entry
    log_entry = {
        "timestamp": started.isoformat() + "Z",
        "status": status,
        "database_type": "postgresql" if is_postgres else "sqlite",
        "backup_path": str(backup_path) if backup_path else None,
        "size_bytes": size_bytes,
        "keep_n": keep_n,
        "pruned_files": pruned,
        "error": error_msg,
    }

    try:
        log_file = backup_dir / "backup_log.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as log_err:
        logger.warning("Could not append to backup_log.jsonl: %s", log_err)

    return log_entry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create timestamped database backup.")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP_N, help="Number of recent backups to keep")
    parser.add_argument("--dir", type=str, default=str(DEFAULT_BACKUP_DIR), help="Target directory for backups")
    args = parser.parse_args()

    res = create_backup(backup_dir=Path(args.dir), keep_n=args.keep)
    print(json.dumps(res, indent=2))
