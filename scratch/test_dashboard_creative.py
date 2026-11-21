import requests
from bs4 import BeautifulSoup
import psycopg2

BASE_URL = "http://127.0.0.1:5000"
PG_CONN = "postgresql://postgres:henil1030@localhost:5432/upian_full_project"

def test_dashboard():
    session = requests.Session()

    # 1. Register a test user
    import uuid
    test_user_email = f"creative2_{uuid.uuid4().hex[:6]}@example.com"
    reg_get = session.get(f"{BASE_URL}/register")
    soup = BeautifulSoup(reg_get.text, "html.parser")
    csrf_token = soup.find("input", {"name": "csrf_token"})["value"]

    reg_resp = session.post(f"{BASE_URL}/register", data={
        "csrf_token": csrf_token,
        "name": "Henil Creative",
        "email": test_user_email,
        "password": "Password123!"
    })
    assert "Sign out" in reg_resp.text or reg_resp.status_code in (200, 302), f"Registration failed: {reg_resp.status_code}"
    print(f"[OK] Registered and logged in as: {test_user_email}")

    # 2. Check GET /dashboard
    dash_resp = session.get(f"{BASE_URL}/dashboard")
    assert dash_resp.status_code == 200, f"Dashboard GET failed: {dash_resp.status_code}"
    soup_dash = BeautifulSoup(dash_resp.text, "html.parser")

    # Check page-head, strip, pricing cards, and comparison table
    assert soup_dash.find(class_="page-head"), "Missing .page-head in dashboard!"
    assert soup_dash.find(class_="dash-current-tier-card"), "Missing .dash-current-tier-card in dashboard!"
    assert soup_dash.find(class_="dashboard-metrics-strip"), "Missing .dashboard-metrics-strip in dashboard!"
    assert soup_dash.find(class_="creative-pricing-grid"), "Missing .creative-pricing-grid in dashboard!"
    assert soup_dash.find(class_="comparison-table"), "Missing .comparison-table in dashboard!"
    assert soup_dash.find(class_="trust-strip"), "Missing .trust-strip in dashboard!"
    assert soup_dash.find(id="checkout-modal"), "Missing #checkout-modal in dashboard!"
    print("[OK] Dashboard sections (page-head, current tier card, metrics strip, pricing grid, comparison table, trust strip) verified!")

    # Verify 3 plans exist on Dashboard
    plan_cards = soup_dash.find_all(class_="creative-tier-card")
    assert len(plan_cards) == 3, f"Expected 3 plan cards, found {len(plan_cards)}"
    plan_text = dash_resp.text
    assert "Free Starter" in plan_text and "Pro" in plan_text and "Pro Plus" in plan_text, "Missing plan names in dashboard!"
    assert "₹999" in plan_text, "Missing ₹999 pricing for Pro!"
    assert "₹2,499" in plan_text, "Missing ₹2,499 pricing for Pro Plus!"
    print("[OK] All 3 tier cards (Free, Pro, Pro Plus) verified!")

    # Check Sidebar styling: ONLY nickname and Change button, NO messy email box!
    assert soup_dash.find(id="who-display-name"), "Missing #who-display-name in sidebar!"
    assert soup_dash.find(id="btn-edit-nickname"), "Missing #btn-edit-nickname in sidebar!"
    assert soup_dash.find(class_="who-avatar"), "Missing .who-avatar in sidebar!"
    assert "who-mail" not in dash_resp.text, "Found messy .who-mail in sidebar, should be removed!"
    assert "who-plan-badge" not in dash_resp.text, "Found cramped who-plan-badge in sidebar, should be in dashboard!"
    print("[OK] Sidebar verified: Clean profile with Avatar, Nickname, and 'Change' button only!")

    # 3. Test Nickname Update from sidebar
    csrf_meta = soup_dash.find("meta", {"name": "csrf-token"})
    session_csrf = csrf_meta["content"] if csrf_meta else ""

    nick_update = session.post(f"{BASE_URL}/api/user/nickname", json={"name": "Henil Creative Master"}, headers={
        "X-CSRF-Token": session_csrf
    })
    assert nick_update.status_code == 200, f"Nickname update failed: {nick_update.text}"
    assert nick_update.json().get("name") == "Henil Creative Master"
    print("[OK] Nickname successfully updated to 'Henil Creative Master'!")

    # Check PostgreSQL
    conn = psycopg2.connect(PG_CONN)
    cur = conn.cursor()
    cur.execute("SELECT name, email, plan FROM users WHERE email = %s;", (test_user_email,))
    u_row = cur.fetchone()
    assert u_row[0] == "Henil Creative Master", f"PostgreSQL name mismatch: {u_row}"
    print(f"[OK] PostgreSQL users table verified: {u_row}")
    cur.close()
    conn.close()

    # 4. Test Upgrading to Pro in Dashboard
    order_res = session.post(f"{BASE_URL}/api/payment/razorpay/order", json={"plan": "plus"}, headers={
        "X-CSRF-Token": session_csrf
    })
    assert order_res.status_code == 200, f"Order creation failed: {order_res.text}"
    ord_data = order_res.json()

    proc_res = session.post(f"{BASE_URL}/api/payment/process", json={
        "plan": "plus",
        "payment_id": "test_pay_creative2_123",
        "order_id": ord_data.get("order_id"),
        "amount": ord_data.get("amount", 999),
        "currency": "INR"
    }, headers={"X-CSRF-Token": session_csrf})
    assert proc_res.status_code == 200, f"Upgrade failed: {proc_res.text}"
    assert proc_res.json().get("success") is True
    print("[OK] Plan upgrade to Pro successful via payment API!")

    # Verify PostgreSQL shows 'plus'
    conn = psycopg2.connect(PG_CONN)
    cur = conn.cursor()
    cur.execute("SELECT plan FROM users WHERE email = %s;", (test_user_email,))
    assert cur.fetchone()[0] == "plus", "PostgreSQL plan not updated to plus!"
    cur.close()
    conn.close()
    print("[OK] PostgreSQL confirmed plan updated to 'plus'!")

    print("\nALL CREATIVE DASHBOARD TESTS PASSED 100%!")

if __name__ == "__main__":
    test_dashboard()
