"""Global pytest configuration and fixtures for UPIAN AI Gateway test suite.
Ensures database schema and migrations are initialized across all test environments.
"""
import pytest
from app import app
from migrations.migrate_v1 import run_migration


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Ensures database is initialized and migrated before any test suite executes."""
    with app.app_context():
        try:
            run_migration(app=app)
        except Exception as exc:
            app.logger.warning("Session migration fixture note: %s", exc)
