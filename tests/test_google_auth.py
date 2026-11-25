import urllib.parse
from unittest.mock import patch, MagicMock
import pytest
from app import app
from models import User, db, upsert_google_user
from config import Config


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_auth_google_login_redirect(client):
    """Test that /auth/google redirects to Google's OAuth endpoint with correct params."""
    res = client.get("/auth/google?next=/activity", follow_redirects=False)
    assert res.status_code == 302
    location = res.headers.get("Location")
    assert location is not None
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)

    assert params["client_id"][0] == Config.GOOGLE_CLIENT_ID
    assert params["response_type"][0] == "code"
    assert "openid" in params["scope"][0]
    assert "state" in params

    with client.session_transaction() as sess:
        assert sess.get("google_auth_next") == "/activity"
        assert sess.get("google_oauth_state") == params["state"][0]


def test_auth_google_callback_error(client):
    """Test handling of error parameter returned from Google."""
    res = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/login")


def test_auth_google_callback_missing_code(client):
    """Test callback without authorization code."""
    res = client.get("/auth/google/callback", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/login")


def test_auth_google_callback_state_mismatch(client):
    """Test CSRF protection when state does not match session."""
    with client.session_transaction() as sess:
        sess["google_oauth_state"] = "legit_state_123"

    res = client.get("/auth/google/callback?code=mock_code&state=tampered_state", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/login")


def test_auth_google_callback_oauth_success(client):
    """Test successful OAuth code exchange and user sign-in."""
    state_token = "valid_state_abc"
    with client.session_transaction() as sess:
        sess["google_oauth_state"] = state_token
        sess["google_auth_next"] = "/chat"

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "mock_access_token",
        "id_token": "mock_id_token",
    }

    mock_idinfo = {
        "email": "oauth_user@example.com",
        "sub": "google_oauth_sub_12345",
        "name": "OAuth User",
    }

    with patch("requests.post", return_value=mock_token_resp), \
         patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_idinfo):
        res = client.get(f"/auth/google/callback?code=good_code&state={state_token}", follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["Location"].endswith("/chat")

    with client.session_transaction() as sess:
        assert sess.get("uid") is not None
        user_id = sess.get("uid")

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user is not None
        assert user.email == "oauth_user@example.com"
        assert user.google_id == "google_oauth_sub_12345"
        assert user.name == "OAuth User"


def test_auth_google_callback_gis_credential_post(client):
    """Test Google Identity Services JWT credential POST."""
    mock_idinfo = {
        "email": "gis_user@example.com",
        "sub": "gis_google_sub_67890",
        "name": "GIS User",
    }

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_idinfo):
        res = client.post(
            "/auth/google/callback",
            json={"credential": "mock_jwt_token_payload"},
            headers={"X-Requested-With": "XMLHttpRequest"}
        )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["user"]["email"] == "gis_user@example.com"

    with client.session_transaction() as sess:
        assert sess.get("uid") == data["user"]["id"]


def test_auth_google_callback_direct_json_post(client):
    """Test direct email and google_id POST (backward compatibility)."""
    payload = {
        "email": "direct_user@example.com",
        "google_id": "direct_sub_9999",
        "name": "Direct User",
    }
    res = client.post("/auth/google/callback", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["user"]["email"] == "direct_user@example.com"


def test_auth_google_account_linking(client):
    """Test that Google login links to existing email account without duplicates."""
    import uuid
    uid_tag = uuid.uuid4().hex[:8]
    test_email = f"link_me_{uid_tag}@example.com"
    with app.app_context():
        existing = User(name="Existing Person", email=test_email, plan="plus")
        existing.set_password("OldPassword123")
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

    payload = {
        "email": test_email,
        "google_id": f"google_linked_sub_{uid_tag}",
        "name": "Existing Person",
    }
    res = client.post("/auth/google/callback", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["user"]["id"] == existing_id

    with app.app_context():
        updated = db.session.get(User, existing_id)
        assert updated.google_id == f"google_linked_sub_{uid_tag}"
        assert updated.plan == "plus"  # Plan is preserved


def test_login_and_register_password_toggle(client):
    """Test that login and register forms contain password visibility toggle button and markup."""
    for endpoint in ["/login", "/register"]:
        res = client.get(endpoint)
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert 'id="password-input"' in html
        assert 'id="toggle-password"' in html
        assert 'class="eye-icon eye-show"' in html
        assert 'class="eye-icon eye-hide"' in html
        assert 'initPasswordToggle' in html

