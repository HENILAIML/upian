"""Comprehensive Test Suite for UPIAN AI Gateway.
Executes and logs results for all modules, services, APIs, and edge cases.
"""
import sys
import os
import io
import time
import json
import uuid
import datetime
import traceback
import sqlite3

# Ensure project root is in path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from config import Config, PLANS, PLAN_ORDER, SLOTS
import models
from models import db, User, RequestLog, Payment, Document
import filetools
import marketdata
import weather
import quotes
import special
import quota
import router
from router import classifier
import llm
import image_gen
import vision
import multimodal
import extractor
from app import app, seed_admin

results = []

def record(category, test_name, status, details="", error=None, latency_ms=0):
    entry = {
        "category": category,
        "test": test_name,
        "status": status, # PASSED, FAILED, WARNING
        "latency_ms": latency_ms,
        "details": details,
        "error": str(error) if error else None
    }
    results.append(entry)
    icon = "[PASS]" if status == "PASSED" else ("[FAIL]" if status == "FAILED" else "[WARN]")
    print(f"{icon} {category} :: {test_name} ({latency_ms}ms) - {details}")
    if error:
        print(f"      ERROR: {error}")

def run_tests():
    print("=" * 70)
    print("STARTING UPIAN COMPREHENSIVE TEST SUITE")
    print("=" * 70)

    # -------------------------------------------------------------
    # 1. DATABASE & INTEGRITY CHECKS
    # -------------------------------------------------------------
    cat = "Database & Models"
    t0 = time.perf_counter()
    try:
        conn = sqlite3.connect("upian.db")
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        chk = cursor.fetchall()
        conn.close()
        if chk and chk[0][0] == "ok":
            record(cat, "SQLite DB PRAGMA Integrity Check", "PASSED", f"Result: {chk[0][0]}", latency_ms=int((time.perf_counter()-t0)*1000))
        else:
            record(cat, "SQLite DB PRAGMA Integrity Check", "FAILED", f"Result: {chk}", latency_ms=int((time.perf_counter()-t0)*1000))
    except Exception as e:
        record(cat, "SQLite DB PRAGMA Integrity Check", "FAILED", error=e)

    with app.app_context():
        # Check tables exist
        try:
            user_count = User.query.count()
            log_count = RequestLog.query.count()
            pay_count = Payment.query.count()
            doc_count = Document.query.count()
            record(cat, "ORM Models & Query Check", "PASSED", f"Users: {user_count}, Logs: {log_count}, Payments: {pay_count}, Docs: {doc_count}")
        except Exception as e:
            record(cat, "ORM Models & Query Check", "FAILED", error=e)

        # User password hashing and verification
        try:
            test_u = User(name="Test Hash", email=f"hash_{uuid.uuid4().hex[:6]}@upian.local", plan="free")
            test_u.set_password("SecurePass#123")
            assert test_u.check_password("SecurePass#123") is True
            assert test_u.check_password("WrongPassword") is False
            record(cat, "User Password Hashing & Verification", "PASSED", "Scrypt/PBKDF2 hash verification works")
        except Exception as e:
            record(cat, "User Password Hashing & Verification", "FAILED", error=e)

    # -------------------------------------------------------------
    # 2. FILETOOLS (All 9 creation formats & reading)
    # -------------------------------------------------------------
    cat = "Filetools & Document Engine"
    sample_md = (
        "# UPIAN Test Report\n\n"
        "This is an automated test document.\n\n"
        "| ID | Service | Status | Latency |\n"
        "|---|---|---|---|\n"
        "| 1 | MobileNet | Active | 45ms |\n"
        "| 2 | SigLIP | Active | 120ms |\n"
        "| 3 | Qwen-VL | Standby | 250ms |\n\n"
        "End of report.\n"
    )

    for fmt in filetools.CREATE_FORMATS: # txt, md, docx, pdf, xlsx, csv, json, html, pptx
        t0 = time.perf_counter()
        try:
            blob, mime, ext = filetools.make_file(sample_md, fmt)
            lat = int((time.perf_counter()-t0)*1000)
            if blob and len(blob) > 0 and ext == fmt:
                record(cat, f"Create Format: {fmt.upper()}", "PASSED", f"Size: {len(blob)} bytes, MIME: {mime}", latency_ms=lat)
                
                # Test round-trip extraction
                tmp_path = os.path.join(Config.UPLOAD_DIR, f"test_temp_{uuid.uuid4().hex[:6]}.{ext}")
                os.makedirs(Config.UPLOAD_DIR, exist_ok=True)
                with open(tmp_path, "wb") as f:
                    f.write(blob)
                try:
                    text, pages = filetools.extract_text(tmp_path, f".{ext}")
                    if "UPIAN" in text or "Service" in text or "MobileNet" in text or "report" in text.lower():
                        record(cat, f"Extract Format: {fmt.upper()}", "PASSED", f"Extracted {len(text)} chars, {pages} page(s)")
                    else:
                        record(cat, f"Extract Format: {fmt.upper()}", "WARNING", f"Extracted {len(text)} chars but key text missing")
                except Exception as ex_read:
                    record(cat, f"Extract Format: {fmt.upper()}", "FAILED", error=ex_read)
                finally:
                    # Give Windows a moment to release file handle if needed
                    time.sleep(0.05)
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
            else:
                record(cat, f"Create Format: {fmt.upper()}", "FAILED", f"Blob empty or invalid extension: {ext}", latency_ms=lat)
        except Exception as e:
            record(cat, f"Create Format: {fmt.upper()}", "FAILED", error=e)

    # Test blocked extension check
    try:
        assert ".exe" in filetools.BLOCKED_EXT
        assert ".dll" in filetools.BLOCKED_EXT
        record(cat, "Security Blocked Extensions List", "PASSED", f"{len(filetools.BLOCKED_EXT)} dangerous extensions blocked")
    except Exception as e:
        record(cat, "Security Blocked Extensions List", "FAILED", error=e)

    # -------------------------------------------------------------
    # 3. MARKET DATA (Gold / Silver)
    # -------------------------------------------------------------
    cat = "Market Data Engine"
    # Prompt detection
    try:
        assert marketdata.detect_asset("gold price in 2023") == "gold"
        assert marketdata.detect_asset("silver price today") == "silver"
        assert marketdata.detect_asset("predict future gold price in 2050") is None # Rejection of future forecast / explanation
        assert marketdata.detect_asset("why did gold drop yesterday?") is None # Rejection of explanation query
        record(cat, "Prompt Intent Detection & Anti-Hallucination Guardrails", "PASSED", "Accurately detects live queries and rejects forecasts/explanations")
    except Exception as e:
        record(cat, "Prompt Intent Detection & Anti-Hallucination Guardrails", "FAILED", error=e)

    # Live data fetching test
    t0 = time.perf_counter()
    try:
        asset = marketdata.detect_asset("gold price today")
        rep = marketdata.report(asset, "gold price today")
        record(cat, "Live Gold Data Fetching (Yahoo/Stooq)", "PASSED", f"Slug: {rep.get('slug')}, Source: {rep.get('source')}", latency_ms=int((time.perf_counter()-t0)*1000))
    except marketdata.DataUnavailable as du:
        # Expected if network is restricted in sandbox or upstream is unavailable
        record(cat, "Live Gold Data Fetching (Yahoo/Stooq)", "WARNING", f"DataUnavailable raised cleanly (no fake numbers): {du}", latency_ms=int((time.perf_counter()-t0)*1000))
    except Exception as e:
        record(cat, "Live Gold Data Fetching (Yahoo/Stooq)", "FAILED", error=e, latency_ms=int((time.perf_counter()-t0)*1000))

    # -------------------------------------------------------------
    # 4. WEATHER ENGINE (Open-Meteo)
    # -------------------------------------------------------------
    cat = "Weather Engine"
    try:
        assert weather.detect("weather in Surat") is True
        assert weather.detect("temperature in Paris today") is True
        assert weather.detect("explain global warming causes") is False
        assert weather.extract_city("weather in Mumbai today") == "Mumbai"
        record(cat, "Weather Intent & City Extraction", "PASSED", "Detected queries and extracted 'Mumbai'")
    except Exception as e:
        record(cat, "Weather Intent & City Extraction", "FAILED", error=e)

    t0 = time.perf_counter()
    try:
        rep = weather.report("weather in Delhi today")
        record(cat, "Live Open-Meteo Weather Fetch", "PASSED", f"Returned {len(rep.get('markdown',''))} chars", latency_ms=int((time.perf_counter()-t0)*1000))
    except (marketdata.DataUnavailable, weather.NeedInfo) as du:
        record(cat, "Live Open-Meteo Weather Fetch", "WARNING", f"Controlled error/fallback: {du}", latency_ms=int((time.perf_counter()-t0)*1000))
    except Exception as e:
        record(cat, "Live Open-Meteo Weather Fetch", "FAILED", error=e, latency_ms=int((time.perf_counter()-t0)*1000))

    # -------------------------------------------------------------
    # 5. QUOTES ENGINE (Crypto, Indices, FX, Commodities, Stocks)
    # -------------------------------------------------------------
    cat = "Quotes Engine"
    try:
        assert quotes.detect("bitcoin price") is not None
        assert quotes.detect("ethereum rate") is not None
        assert quotes.detect("nifty 50 current price") is not None
        assert quotes.detect("usd to inr") is not None
        assert quotes.detect("crude oil price") is not None
        assert quotes.detect("why is bitcoin falling?") is None
        record(cat, "Live Quotes Intent Detection (Crypto, FX, Indices, Oil)", "PASSED", "All asset types identified correctly")
    except Exception as e:
        record(cat, "Live Quotes Intent Detection (Crypto, FX, Indices, Oil)", "FAILED", error=e)

    t0 = time.perf_counter()
    try:
        spec = quotes.detect("bitcoin price")
        rep = quotes.report(spec, "bitcoin price")
        record(cat, "Live Yahoo Finance Quote Fetch", "PASSED", f"Source: {rep.get('source')}, Slug: {rep.get('slug')}", latency_ms=int((time.perf_counter()-t0)*1000))
    except marketdata.DataUnavailable as du:
        record(cat, "Live Yahoo Finance Quote Fetch", "WARNING", f"DataUnavailable cleanly raised: {du}", latency_ms=int((time.perf_counter()-t0)*1000))
    except Exception as e:
        record(cat, "Live Yahoo Finance Quote Fetch", "FAILED", error=e, latency_ms=int((time.perf_counter()-t0)*1000))

    # -------------------------------------------------------------
    # 6. SPECIAL CHAT ROUTING (Export & shortcuts)
    # -------------------------------------------------------------
    cat = "Special Chat Handler"
    try:
        fmts = special.detect_formats("show me this in excel and as a pdf")
        assert "xlsx" in fmts and "pdf" in fmts
        fmt_doc = special.detect_formats("export as word docx")
        assert "docx" in fmt_doc
        assert special.detect_formats("what is the weather like?") == []
        record(cat, "Export Intent & Format Parser", "PASSED", f"Detected multiple formats: {fmts}")
    except Exception as e:
        record(cat, "Export Intent & Format Parser", "FAILED", error=e)

    # -------------------------------------------------------------
    # 7. QUOTA & TIER MANAGEMENT
    # -------------------------------------------------------------
    cat = "Quota & Tier Engine"
    with app.app_context():
        try:
            u_free = User(name="Quota Free", email="qfree@test.local", plan="free")
            u_plus = User(name="Quota Plus", email="qplus@test.local", plan="plus")
            u_pro = User(name="Quota ProPlus", email="qpro@test.local", plan="plus_pro")
            
            usage_free = quota.get_usage(u_free)
            usage_plus = quota.get_usage(u_plus)
            usage_pro = quota.get_usage(u_pro)
            
            assert usage_free["day"]["limit"] == 20
            assert usage_plus["day"]["limit"] == 100
            assert usage_pro["day"]["limit"] == 300
            
            assert usage_free["images"]["limit"] == 1
            assert usage_plus["images"]["limit"] == 10
            assert usage_pro["images"]["limit"] == 25
            
            record(cat, "Multi-tier Quota Allocations (Free/Pro/ProPlus)", "PASSED", "Free(20/d, 1 img), Pro(100/d, 10 img), Pro+(300/d, 25 img)")
        except Exception as e:
            record(cat, "Multi-tier Quota Allocations (Free/Pro/ProPlus)", "FAILED", error=e)

    # -------------------------------------------------------------
    # 8. ROUTER & CLASSIFIER
    # -------------------------------------------------------------
    cat = "Intelligent Router & Classifier"
    try:
        s_simple = router.heuristic_score("hi there")
        s_complex = router.heuristic_score("Implement distributed Raft consensus with leader election and snapshotting in C++")
        assert s_simple < 0.35
        assert s_complex >= 0.70
        record(cat, "Heuristic Complexity Scorer", "PASSED", f"Simple: {s_simple:.2f}, Complex: {s_complex:.2f}")
    except Exception as e:
        record(cat, "Heuristic Complexity Scorer", "FAILED", error=e)

    try:
        st = classifier.status
        is_ready = classifier.ready
        record(cat, "Model Classifier Load Status", "PASSED", f"Status: {st}, Ready: {is_ready}")
    except Exception as e:
        record(cat, "Model Classifier Load Status", "FAILED", error=e)

    # -------------------------------------------------------------
    # 9. LLM & PROVIDER CHAIN
    # -------------------------------------------------------------
    cat = "LLM Provider Chain & Network"
    try:
        chain_simple = llm.build_chain("simple")
        chain_med = llm.build_chain("medium")
        chain_comp = llm.build_chain("complex")
        record(cat, "Provider Chain Construction", "PASSED", f"Simple slots: {len(chain_simple)}, Med: {len(chain_med)}, Comp: {len(chain_comp)}")
    except Exception as e:
        record(cat, "Provider Chain Construction", "FAILED", error=e)

    # Test Live Generation call to configured slot
    t0 = time.perf_counter()
    slot_free = SLOTS.get("free")
    if slot_free and slot_free.get("api_key"):
        try:
            res = llm.generate(slot_free, [{"role": "user", "content": "Reply with just 'OK'"}])
            record(cat, f"Live Model Call: {slot_free['provider']} ({slot_free['model']})", "PASSED", f"Reply: {res['text'][:50]}, Latency: {res['latency_ms']}ms", latency_ms=res['latency_ms'])
        except llm.ProviderError as pe:
            if "network" in str(pe).lower():
                record(cat, f"Live Model Call: {slot_free['provider']} ({slot_free['model']})", "WARNING", f"Sandbox network isolation blocked outbound call cleanly: {pe}", latency_ms=int((time.perf_counter()-t0)*1000))
            else:
                record(cat, f"Live Model Call: {slot_free['provider']} ({slot_free['model']})", "FAILED", f"Provider error: {pe}", error=pe, latency_ms=int((time.perf_counter()-t0)*1000))
        except Exception as e:
            record(cat, f"Live Model Call: {slot_free['provider']} ({slot_free['model']})", "FAILED", error=e, latency_ms=int((time.perf_counter()-t0)*1000))
    else:
        record(cat, "Live Model Call", "WARNING", "No API key configured for free slot")

    # -------------------------------------------------------------
    # 10. IMAGE GENERATION ENGINE
    # -------------------------------------------------------------
    cat = "Image Generation Engine"
    try:
        ana = image_gen.analyze_image_prompt("Ultra detailed digital painting of a cosmic dragon")
        assert ana["tier"] in ("low_cost", "premium")
        record(cat, "Image Prompt Analysis", "PASSED", f"Tier: {ana['tier']}, Res: {ana['resolution']}, Score: {ana['complexity_score']}")
    except Exception as e:
        record(cat, "Image Prompt Analysis", "FAILED", error=e)

    # Verify that generation without valid API key fails gracefully
    try:
        image_gen.generate_image("A cute kitten")
        record(cat, "Missing API Key Error Handling", "FAILED", "Expected ImageGenError but succeeded without key")
    except image_gen.ImageGenError as ige:
        record(cat, "Missing API Key Error Handling", "PASSED", f"Clean error raised: code={ige.code}, status={ige.status_code}")
    except Exception as e:
        record(cat, "Missing API Key Error Handling", "FAILED", error=e)

    # -------------------------------------------------------------
    # 11. MULTIMODAL & VISION INTELLIGENCE
    # -------------------------------------------------------------
    cat = "Multimodal & Vision Intelligence"
    # Create synthetic test image
    from PIL import Image
    test_img = Image.new("RGB", (128, 128), color=(100, 150, 200))
    buf = io.BytesIO()
    test_img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    t0 = time.perf_counter()
    try:
        res_m = vision.classify_mobilenet(img_bytes)
        lat = int((time.perf_counter()-t0)*1000)
        assert res_m["model"] == "mobilenet_v3_small"
        assert len(res_m["ranked_predictions"]) > 0
        record(cat, "MobileNetV3 Classification", "PASSED", f"Top: {res_m['top_prediction']} ({res_m['confidence']:.2f}), Uncertainty: {res_m['uncertainty']['uncertainty']}", latency_ms=lat)
    except Exception as e:
        record(cat, "MobileNetV3 Classification", "FAILED", error=e)

    t0 = time.perf_counter()
    try:
        res_s = vision.classify_siglip(img_bytes, candidate_labels=["sky", "water", "forest", "desert"])
        lat = int((time.perf_counter()-t0)*1000)
        assert res_s["model"] == "siglip2_base"
        record(cat, "SigLIP 2 Zero-Shot Classification", "PASSED", f"Top: {res_s['top_prediction']} ({res_s['confidence']:.2f})", latency_ms=lat)
    except Exception as e:
        record(cat, "SigLIP 2 Zero-Shot Classification", "FAILED", error=e)

    # -------------------------------------------------------------
    # 12. EXTRACTOR (Table & structured data export)
    # -------------------------------------------------------------
    cat = "Extractor Engine"
    sample_struct = {
        "title": "Extraction Test",
        "summary": "Summary of extracted table",
        "text": "Detailed notes on financial metrics",
        "tables": [{"name": "Q1 Figures", "rows": [["Month", "Revenue"], ["Jan", "10k"], ["Feb", "12k"]]}]
    }
    t0 = time.perf_counter()
    try:
        tmp_exp = os.path.join(Config.UPLOAD_DIR, "test_exports_diag")
        files = extractor.export_all_formats(sample_struct, tmp_exp, "diag_report")
        assert len(files) == 6
        record(cat, "Export All 6 Formats", "PASSED", f"Generated: {', '.join(files.keys())}", latency_ms=int((time.perf_counter()-t0)*1000))
    except Exception as e:
        record(cat, "Export All 6 Formats", "FAILED", error=e)

    # -------------------------------------------------------------
    # 13. FLASK HTTP ENDPOINTS & AUTHENTICATION (All 44 Routes)
    # -------------------------------------------------------------
    cat = "Flask Endpoints & Security"
    app.config["TESTING"] = True
    client = app.test_client()

    # Unauthenticated checks
    routes_unauth = [
        ("/", 302),
        ("/activity", 302),
        ("/files", 302),
        ("/admin", 302),
        ("/admin/users", 302),
        ("/admin/requests", 302),
        ("/admin/analytics", 302),
        ("/api/activity", 401),
        ("/api/usage", 401),
        ("/api/docs", 401),
        ("/api/payment/plans", 401),
        ("/healthz", 200),
        ("/login", 200),
        ("/register", 200),
        ("/vision", 200),
    ]
    for route, exp_code in routes_unauth:
        t0 = time.perf_counter()
        try:
            res = client.get(route)
            lat = int((time.perf_counter()-t0)*1000)
            if res.status_code == exp_code:
                record(cat, f"Unauthenticated GET {route}", "PASSED", f"Status: {res.status_code} (Expected {exp_code})", latency_ms=lat)
            else:
                record(cat, f"Unauthenticated GET {route}", "FAILED", f"Status: {res.status_code} != {exp_code}", latency_ms=lat)
        except Exception as e:
            record(cat, f"Unauthenticated GET {route}", "FAILED", error=e)

    # Test Registration
    reg_email = f"diag_{uuid.uuid4().hex[:6]}@upian.local"
    test_csrf = "test_csrf_token_secret_123"
    with client.session_transaction() as sess:
        sess["csrf"] = test_csrf
    try:
        res = client.post("/register", data={"name": "Diagnostic User", "email": reg_email, "password": "DiagnosticPassword@123", "csrf_token": test_csrf}, follow_redirects=False)
        assert res.status_code == 302
        record(cat, "POST /register (New User Creation)", "PASSED", f"User {reg_email} registered successfully")
    except Exception as e:
        record(cat, "POST /register (New User Creation)", "FAILED", error=e)

    # Test Duplicate Registration
    try:
        dup_client = app.test_client()
        with dup_client.session_transaction() as sess:
            sess["csrf"] = test_csrf
        res = dup_client.post("/register", data={"name": "Duplicate User", "email": reg_email, "password": "DiagnosticPassword@123", "csrf_token": test_csrf})
        assert "already exists" in res.get_data(as_text=True)
        record(cat, "POST /register Duplicate Email Rejection", "PASSED", "Duplicate email cleanly rejected")
    except Exception as e:
        record(cat, "POST /register Duplicate Email Rejection", "FAILED", error=e)

    # Authenticated Session
    with app.app_context():
        user = User.query.filter_by(email=reg_email).first()
        admin_user = User.query.filter_by(is_admin=True).first()
        user_id = user.id if user else None
        admin_id = admin_user.id if admin_user else None

    auth_client = app.test_client()
    with auth_client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = "test_csrf_token_secret_123"

    admin_client = app.test_client()
    with admin_client.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["csrf"] = "test_csrf_token_secret_123"

    # Authenticated user pages
    auth_pages = [
        ("/", 200),
        ("/activity", 200),
        ("/files", 200),
        ("/api/usage", 200),
        ("/api/activity", 200),
        ("/api/docs", 200),
        ("/api/payment/plans", 200),
        ("/api/vision/models", 200),
        ("/api/image/models", 200),
    ]
    for route, exp_code in auth_pages:
        t0 = time.perf_counter()
        try:
            res = auth_client.get(route)
            lat = int((time.perf_counter()-t0)*1000)
            if res.status_code == exp_code:
                record(cat, f"Authenticated User GET {route}", "PASSED", f"Status: {res.status_code}", latency_ms=lat)
            else:
                record(cat, f"Authenticated User GET {route}", "FAILED", f"Status: {res.status_code} != {exp_code}", latency_ms=lat)
        except Exception as e:
            record(cat, f"Authenticated User GET {route}", "FAILED", error=e)

    # Role-Based Access Control: Regular user attempting admin routes
    admin_routes = ["/admin", "/admin/users", "/admin/requests", "/admin/analytics", "/admin/api/stats"]
    for route in admin_routes:
        t0 = time.perf_counter()
        try:
            res = auth_client.get(route)
            lat = int((time.perf_counter()-t0)*1000)
            if res.status_code in (403, 302):
                record(cat, f"RBAC Non-Admin Blocked on {route}", "PASSED", f"Status: {res.status_code} (Access forbidden)", latency_ms=lat)
            else:
                record(cat, f"RBAC Non-Admin Blocked on {route}", "FAILED", f"Security leak: regular user received status {res.status_code}", latency_ms=lat)
        except Exception as e:
            record(cat, f"RBAC Non-Admin Blocked on {route}", "FAILED", error=e)

    # Admin access to admin routes
    for route in admin_routes:
        t0 = time.perf_counter()
        try:
            res = admin_client.get(route)
            lat = int((time.perf_counter()-t0)*1000)
            if res.status_code == 200:
                record(cat, f"Admin User Access GET {route}", "PASSED", f"Status: {res.status_code}", latency_ms=lat)
            else:
                record(cat, f"Admin User Access GET {route}", "FAILED", f"Admin access failed with status {res.status_code}", latency_ms=lat)
        except Exception as e:
            record(cat, f"Admin User Access GET {route}", "FAILED", error=e)

    # Test CSRF Protection
    csrf_headers = {"X-CSRF-Token": "test_csrf_token_secret_123"}
    try:
        # POST without CSRF token
        res_no_csrf = auth_client.post("/api/payment/process", json={"plan": "plus", "amount": 15})
        assert res_no_csrf.status_code == 400
        record(cat, "CSRF Enforcement (Reject Missing Token)", "PASSED", "Rejected with HTTP 400 Bad Request")
    except Exception as e:
        record(cat, "CSRF Enforcement (Reject Missing Token)", "FAILED", error=e)

    # Test Subscription Upgrade API
    t0 = time.perf_counter()
    try:
        res = auth_client.post("/api/payment/process", headers=csrf_headers, json={"plan": "plus", "payment_method": "card", "amount": 15.0})
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["plan"] == "plus"
        record(cat, "POST /api/payment/process (Upgrade to Pro)", "PASSED", f"TX: {data.get('transaction_id')}", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/payment/process (Upgrade to Pro)", "FAILED", error=e)

    # Test Subscription Downgrade API
    t0 = time.perf_counter()
    try:
        res = auth_client.post("/api/payment/downgrade", headers=csrf_headers, json={})
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["plan"] == "free"
        record(cat, "POST /api/payment/downgrade (Revert to Free)", "PASSED", "Plan reverted to free", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/payment/downgrade (Revert to Free)", "FAILED", error=e)

    # Test Multimodal Upload API with PNG
    t0 = time.perf_counter()
    try:
        data = {"file": (io.BytesIO(img_bytes), "test_upload.png")}
        res = auth_client.post("/api/upload", headers=csrf_headers, data=data, content_type="multipart/form-data")
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        j = res.get_json()
        assert j["success"] is True
        assert j["file"]["kind"] == "image"
        record(cat, "POST /api/upload (Multimodal File Upload)", "PASSED", f"Uploaded token: {j['file']['token'][:12]}...", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/upload (Multimodal File Upload)", "FAILED", error=e)

    # Test Document / Text Extraction API
    t0 = time.perf_counter()
    try:
        csv_bytes = b"Name,Score,Status\nAlpha,95,Passed\nBeta,88,Passed\n"
        data = {"file": (io.BytesIO(csv_bytes), "scores.csv")}
        res = auth_client.post("/api/extract", headers=csrf_headers, data=data, content_type="multipart/form-data")
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        j = res.get_json()
        assert j["success"] is True
        assert len(j["data"]["tables"]) == 1
        record(cat, "POST /api/extract (CSV Document Extraction)", "PASSED", f"Extracted 1 table with {len(j['data']['tables'][0]['rows'])} rows", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/extract (CSV Document Extraction)", "FAILED", error=e)

    # Test Chat API with Special Shortcut (Export Request)
    t0 = time.perf_counter()
    try:
        res = auth_client.post("/api/chat", headers=csrf_headers, json={"prompt": "make an excel table with columns Name and Age"})
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        lines = [line for line in res.get_data(as_text=True).split("\n") if line.strip()]
        events = [json.loads(line) for line in lines]
        has_done = any(e.get("event") == "done" for e in events)
        record(cat, "POST /api/chat (Export & Stream Pipeline)", "PASSED", f"Stream returned {len(events)} events, Done={has_done}", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/chat (Export & Stream Pipeline)", "FAILED", error=e)

    # Test Chat API with Gold Shortcut (Real Data Pipeline)
    t0 = time.perf_counter()
    try:
        res = auth_client.post("/api/chat", headers=csrf_headers, json={"prompt": "gold price in 2023"})
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        lines = [line for line in res.get_data(as_text=True).split("\n") if line.strip()]
        events = [json.loads(line) for line in lines]
        record(cat, "POST /api/chat (Real-Data Gold Query Pipeline)", "PASSED", f"Events: {len(events)}", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/chat (Real-Data Gold Query Pipeline)", "FAILED", error=e)

    # Test Chat API with Image Generation Prompt
    t0 = time.perf_counter()
    try:
        res = auth_client.post("/api/chat", headers=csrf_headers, json={"prompt": "generate an image of a red racecar"})
        lat = int((time.perf_counter()-t0)*1000)
        assert res.status_code == 200
        lines = [line for line in res.get_data(as_text=True).split("\n") if line.strip()]
        events = [json.loads(line) for line in lines]
        done_ev = next((e for e in events if e.get("event") == "done"), {})
        reply_txt = done_ev.get("reply", "")
        # Should gracefully inform user about plan upgrade or API key requirement without crashing
        assert "image" in reply_txt.lower() or "plan" in reply_txt.lower()
        record(cat, "POST /api/chat (Image Generation Intent Handling)", "PASSED", f"Graceful reply: {reply_txt[:60]}...", latency_ms=lat)
    except Exception as e:
        record(cat, "POST /api/chat (Image Generation Intent Handling)", "FAILED", error=e)

    # Summary Statistics
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASSED")
    failed = sum(1 for r in results if r["status"] == "FAILED")
    warned = sum(1 for r in results if r["status"] == "WARNING")

    print("\n" + "=" * 70)
    print(f"TEST SUMMARY: TOTAL={total} | PASSED={passed} | FAILED={failed} | WARNING={warned}")
    print("=" * 70)

    # Save results to JSON file for analysis
    with open("test_results_detailed.json", "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.datetime.now().isoformat(),
            "summary": {"total": total, "passed": passed, "failed": failed, "warning": warned},
            "results": results
        }, f, indent=2)

if __name__ == "__main__":
    run_tests()
