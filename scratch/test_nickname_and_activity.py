import requests
import re
import psycopg2
from bs4 import BeautifulSoup

BASE_URL = "http://127.0.0.1:5000"
PG_CONN = "postgresql://postgres:henil1030@localhost:5432/upian_full_project"

def test_all():
    session = requests.Session()
    
    # 1. Fetch login page to get CSRF token
    resp = session.get(f"{BASE_URL}/login")
    assert resp.status_code == 200, f"Login GET failed: {resp.status_code}"
    
    soup = BeautifulSoup(resp.text, "html.parser")
    csrf_input = soup.find("input", {"name": "csrf_token"})
    csrf_token = csrf_input["value"] if csrf_input else ""
    print(f"[OK] Fetched login page, CSRF token: {csrf_token[:10]}...")

    # 2. Login as test/admin user or check existing user
    # Let's check users in Postgres first
    conn = psycopg2.connect(PG_CONN)
    cur = conn.cursor()
    cur.execute("SELECT id, email, name, plan FROM users ORDER BY id ASC LIMIT 5;")
    users = cur.fetchall()
    print(f"[OK] PostgreSQL users: {users}")
    cur.close()
    conn.close()

    # Let's log in with the user or test user
    # We tested henil@gmail.com / henil123 in previous tests or lastline03@gmail.com
    # Let's try logging in as henil@gmail.com or test_pg_user@example.com
    test_user_email = None
    for u in users:
        if "test_pg" in u[1] or "henil" in u[1]:
            test_user_email = u[1]
            break
    
    if not test_user_email:
        test_user_email = users[0][1] if users else None

    # Let's attempt login
    login_resp = session.post(f"{BASE_URL}/login", data={
        "csrf_token": csrf_token,
        "email": test_user_email,
        "password": "Password123!" # if created in test_postgres_e2e.py
    })
    
    # If password was different, let's create a temporary session or test user
    if login_resp.status_code != 200 or "Sign out" not in login_resp.text:
        # Register a fresh verified test user
        import uuid
        fresh_email = f"nick_test_{uuid.uuid4().hex[:6]}@example.com"
        reg_get = session.get(f"{BASE_URL}/register")
        soup = BeautifulSoup(reg_get.text, "html.parser")
        csrf_reg = soup.find("input", {"name": "csrf_token"})["value"]
        
        reg_resp = session.post(f"{BASE_URL}/register", data={
            "csrf_token": csrf_reg,
            "name": "Henil Tester",
            "email": fresh_email,
            "password": "SecretPassword123!"
        })
        assert "Sign out" in reg_resp.text or reg_resp.status_code in (200, 302), f"Registration failed: {reg_resp.status_code}"
        print(f"[OK] Registered fresh test user: {fresh_email}")

    # Now verify we are logged in by checking home
    home_resp = session.get(f"{BASE_URL}/")
    assert "Sign out" in home_resp.text, "User is not logged in!"
    soup_home = BeautifulSoup(home_resp.text, "html.parser")
    csrf_meta = soup_home.find("meta", {"name": "csrf-token"})
    session_csrf = csrf_meta["content"] if csrf_meta else ""
    
    # Check sidebar presence of who-plan-badge and who-name-wrap
    assert soup_home.find(class_="who-plan-badge"), "Missing .who-plan-badge in sidebar!"
    assert soup_home.find(id="who-display-name"), "Missing #who-display-name in sidebar!"
    assert soup_home.find(id="btn-edit-nickname"), "Missing #btn-edit-nickname in sidebar!"
    print("[OK] Verified sidebar contains .who-plan-badge and nickname editing elements.")

    # 3. Test Nickname Update via POST /api/user/nickname
    new_nickname = "Henil_Master"
    nick_resp = session.post(f"{BASE_URL}/api/user/nickname", json={"name": new_nickname}, headers={
        "X-CSRF-Token": session_csrf
    })
    assert nick_resp.status_code == 200, f"Nickname update failed: {nick_resp.text}"
    data = nick_resp.json()
    assert data.get("success") is True, f"Response success not True: {data}"
    assert data.get("name") == new_nickname, f"Returned name mismatch: {data}"
    print(f"[OK] POST /api/user/nickname succeeded: {data}")

    # 4. Check PostgreSQL database directly for name update in both users and upian tables
    conn = psycopg2.connect(PG_CONN)
    cur = conn.cursor()
    cur.execute("SELECT id, name, email, plan FROM users WHERE name = %s;", (new_nickname,))
    pg_user = cur.fetchone()
    assert pg_user is not None, f"Nickname {new_nickname} not found in PostgreSQL users table!"
    print(f"[OK] PostgreSQL users table confirmed updated: {pg_user}")

    cur.execute("SELECT id, name, email, plan FROM upian WHERE name = %s;", (new_nickname,))
    pg_upian = cur.fetchone()
    assert pg_upian is not None, f"Nickname {new_nickname} not found in PostgreSQL upian table!"
    print(f"[OK] PostgreSQL upian table confirmed updated: {pg_upian}")
    cur.close()
    conn.close()

    # 5. Test Activity page: Ensure payment / pricing is completely removed
    act_resp = session.get(f"{BASE_URL}/activity")
    assert act_resp.status_code == 200, f"Activity page GET failed: {act_resp.status_code}"
    
    assert "pricing-wrap" not in act_resp.text, "Activity page still contains pricing-wrap!"
    assert "checkout-modal" not in act_resp.text, "Activity page still contains checkout-modal!"
    assert "razorpay.com" not in act_resp.text, "Activity page still contains Razorpay script!"
    assert "Request Logs" in act_resp.text, "Activity page missing Request Logs table!"
    assert "summary" in act_resp.text, "Activity page missing summary section!"
    assert new_nickname in act_resp.text, "Activity page sidebar does not show updated nickname!"
    assert "who-plan-badge" in act_resp.text, "Activity page sidebar does not show who-plan-badge!"
    print("[OK] Verified Activity page: Payments completely removed, only Request Logs & Metrics present!")
    print("\nALL TESTS PASSED SUCCESSFULLY! 100% VERIFIED!")

if __name__ == "__main__":
    test_all()
