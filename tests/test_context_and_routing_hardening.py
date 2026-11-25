
"""Integration & End-to-End Tests for:
1. Fast, short answers for frequent casual questions.
2. Context memory & entity resolution.
3. Strong prompt routing and intelligent classification.
"""

import json
import pytest
from app import app, db
from models import User
import router
import llm
import special
import marketdata


TEST_CSRF = "secure_test_csrf_token_routing_ctx_111"


@pytest.fixture
def auth_client():
    app.config["TESTING"] = True

    with app.test_client() as client:
        with app.app_context():
            user = User.query.filter_by(
                email="ctx_router_tester@upian.local"
            ).first()

            if not user:
                user = User(
                    name="Context Router Tester",
                    email="ctx_router_tester@upian.local",
                    plan="pro"
                )
                db.session.add(user)
                db.session.commit()
            else:
                user.plan = "pro"
                db.session.commit()

            uid = user.id

            from models import RequestLog
            RequestLog.query.filter_by(user_id=uid).delete()
            db.session.commit()

        with client.session_transaction() as sess:
            sess["uid"] = uid
            sess["csrf"] = TEST_CSRF

        yield client, uid


# =============================================================================
# 1. Frequent Casual Questions
# =============================================================================

def test_casual_greeting_detection():
    greetings = [
        "hi", "hello", "hey", "how are you",
        "how are you doing", "good morning", "thanks", "ok"
    ]

    for greeting in greetings:
        is_casual, reply = llm.is_casual_greeting(greeting)

        assert is_casual is True
        assert len(reply) > 0
        assert len(reply.split()) <= 20

    non_casual = [
        "what is quantum computing?",
        "write a python function to sort",
        "explain microservices"
    ]

    for query in non_casual:
        is_casual, _ = llm.is_casual_greeting(query)
        assert is_casual is False


def test_casual_greeting_chat_endpoint(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    res = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": "hi"}
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "route" in events
    assert "done" in events
    assert events["route"]["tier"] in ("simple", "weak")
    assert "Hello" in events["done"]["reply"]
    assert len(events["done"]["reply"].split()) <= 15

    res2 = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": "how are you"}
    )

    assert res2.status_code == 200

    lines2 = [
        json.loads(line)
        for line in res2.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events2 = {line.get("event"): line for line in lines2}

    assert (
        "doing great" in events2["done"]["reply"]
        or "doing well" in events2["done"]["reply"]
    )


# =============================================================================
# 2. Context Memory & Entity Continuity
# =============================================================================

def test_gold_price_to_indian_price_context_resolution():
    history = [
        {"role": "user", "content": "what is the price of gold?"},
        {
            "role": "assistant",
            "content": "### Gold price now (USD per troy ounce)\n\n| Date | Price |\n| 2026-11-23 | $2,715.50 |"
        }
    ]

    queries = [
        "what is the Indian price?",
        "Indian price",
        "in India?",
        "share price in India"
    ]

    for query in queries:
        asset = marketdata.detect_asset(query, history=history)
        assert asset == "gold"

    res = special.handle(
        1,
        "what is the Indian price?",
        history
    )

    assert res is not None
    assert "Gold price now in INR per 10 grams" in res["reply"]
    assert "₹" in res["reply"]


def test_gold_price_followup_chat_endpoint(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    history = [
        {"role": "user", "content": "what is the price of gold?"},
        {
            "role": "assistant",
            "content": "### Gold price now (USD per troy ounce)\n\n| Date | Price |\n| 2026-11-23 | $2,715.50 |"
        }
    ]

    payload = {
        "prompt": "what about Indian price?",
        "history": history
    }

    res = client.post(
        "/api/chat",
        headers=headers,
        json=payload
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "done" in events

    reply = events["done"]["reply"]

    assert "Gold" in reply
    assert "INR" in reply or "₹" in reply


# =============================================================================
# 3. Strong Prompt Classification & Routing
# =============================================================================

def test_strong_prompt_classification_and_routing():
    query1 = (
        "Design a scalable microservices architecture for a payments "
        "platform with idempotency, retries and eventual consistency."
    )

    res1 = router.classifier.classify(query1)

    assert res1["tier"] in ("complex", "medium")
    assert res1["legacy_tier"] == "strong"
    assert res1["score"] >= 0.50

    query2 = (
        "strong prompt: optimize this database query "
        "and explain execution plan"
    )

    res2 = router.classifier.classify(query2)

    assert res2["tier"] == "complex"
    assert res2["legacy_tier"] == "strong"
    assert res2["score"] >= 0.70
    assert (
        "gemini-2.5-pro" in res2["suggested_model"]
        or "pro" in res2["suggested_model"].lower()
    )


def test_strong_prompt_offline_mock_generation():
    slot = {
        "name": "mock_complex",
        "provider": "mock",
        "model": "gemini-2.5-pro",
        "api_key": "",
        "base_url": ""
    }

    messages = [
        {
            "role": "user",
            "content": (
                "Write a Python implementation of a thread-safe "
                "LRU cache with time complexity."
            )
        }
    ]

    result = llm.generate(slot, messages)

    assert result["text"]
    assert (
        "ThreadSafeLRUCache" in result["text"]
        or "OrderedDict" in result["text"]
    )
    assert "Complexity Analysis" in result["text"]
    assert "O(1)" in result["text"]
    assert result["latency_ms"] < 200


def test_strong_prompt_chat_endpoint(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    payload = {
        "prompt": (
            "strong prompt: Design a scalable microservices "
            "architecture for payment processing with idempotency."
        )
    }

    res = client.post(
        "/api/chat",
        headers=headers,
        json=payload
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "route" in events
    assert "done" in events
    assert events["route"]["legacy_tier"] == "strong"

    reply = events["done"]["reply"]

    assert "Architecture" in reply or "Idempotency" in reply


# =============================================================================
# 4. Commodities, Crypto, Stocks & Currency
# =============================================================================

def test_real_objects_commodities_crypto_stocks_detection_and_quotes():
    import quotes

    commodities = [
        ("crude oil price", "WTI crude oil"),
        ("brent crude price", "Brent crude oil"),
        ("natural gas price", "Natural gas"),
        ("copper price", "Copper"),
        ("aluminum price", "Aluminum"),
        ("zinc price", "Zinc"),
        ("coffee price", "Coffee"),
        ("wheat price", "Wheat"),
        ("sugar price", "Sugar")
    ]

    for prompt, expected_label in commodities:
        spec = quotes.detect(prompt)

        assert spec is not None
        assert spec["label"] == expected_label

        rep = quotes.report(spec, prompt)

        assert expected_label in rep["markdown"]
        assert "Price" in rep["markdown"]

    cryptos = [
        ("bitcoin price", "Bitcoin"),
        ("ethereum rate", "Ethereum"),
        ("solana price", "Solana"),
        ("dogecoin price", "Dogecoin"),
        ("ripple price", "XRP")
    ]

    for prompt, expected_label in cryptos:
        spec = quotes.detect(prompt)

        assert spec is not None
        assert spec["label"] == expected_label

        rep = quotes.report(spec, prompt)

        assert expected_label in rep["markdown"]

    equities = [
        ("nifty 50 price", "Nifty 50"),
        ("sensex price", "BSE Sensex"),
        ("s&p 500 price", "S&P 500"),
        ("reliance share price", "reliance"),
        ("apple stock price", "apple")
    ]

    for prompt, expected_label in equities:
        spec = quotes.detect(prompt)

        assert spec is not None

        rep = quotes.report(spec, prompt)

        assert "price" in rep["markdown"].lower()


def test_real_objects_direct_inr_conversion():
    import quotes

    spec = quotes.detect("bitcoin price in inr")

    assert spec is not None
    assert spec["target_ccy"] == "INR"

    rep = quotes.report(spec, "bitcoin price in inr")

    assert "₹" in rep["markdown"]
    assert "USD & INR" in rep["markdown"]
    assert "Exchange Rate" in rep["markdown"]

    spec_oil = quotes.detect("crude oil price in inr")

    assert spec_oil is not None
    assert spec_oil["target_ccy"] == "INR"

    rep_oil = quotes.report(
        spec_oil,
        "crude oil price in inr"
    )

    assert "₹" in rep_oil["markdown"]
    assert "WTI crude oil" in rep_oil["markdown"]


def test_real_objects_context_followup_resolution():
    import quotes

    history_btc = [
        {"role": "user", "content": "what is the price of bitcoin?"},
        {
            "role": "assistant",
            "content": "### Bitcoin price now (USD)\n\n| Time | Price (USD) |\n| Now | $88,450.00 |"
        }
    ]

    spec_btc = quotes.detect(
        "what about in INR?",
        history=history_btc
    )

    assert spec_btc is not None
    assert spec_btc["label"] == "Bitcoin"
    assert spec_btc["target_ccy"] == "INR"

    rep_btc = quotes.report(
        spec_btc,
        "what about in INR?"
    )

    assert "₹" in rep_btc["markdown"]
    assert "Bitcoin" in rep_btc["markdown"]

    history_aapl = [
        {"role": "user", "content": "what is apple stock price?"},
        {
            "role": "assistant",
            "content": "### Apple Inc. price now (USD)\n\n| Time | Price (USD) |\n| Now | $228.50 |"
        }
    ]

    spec_aapl = quotes.detect(
        "Indian price",
        history=history_aapl
    )

    assert spec_aapl is not None
    assert spec_aapl["target_ccy"] == "INR"

    rep_aapl = quotes.report(spec_aapl, "Indian price")

    assert "₹" in rep_aapl["markdown"]
    assert "Apple" in rep_aapl["markdown"]

    history_oil = [
        {"role": "user", "content": "crude oil price"},
        {
            "role": "assistant",
            "content": "### WTI crude oil price now (USD per barrel)\n\n| Time | Price |\n| Now | $69.25 |"
        }
    ]

    spec_oil = quotes.detect(
        "in rupees?",
        history=history_oil
    )

    assert spec_oil is not None
    assert spec_oil["label"] == "WTI crude oil"
    assert spec_oil["target_ccy"] == "INR"

    rep_oil = quotes.report(spec_oil, "in rupees?")

    assert "₹" in rep_oil["markdown"]
    assert "WTI crude oil" in rep_oil["markdown"]


def test_real_objects_chat_endpoint(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    res1 = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": "bitcoin price"}
    )

    assert res1.status_code == 200

    lines1 = [
        json.loads(line)
        for line in res1.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events1 = {line.get("event"): line for line in lines1}

    assert "done" in events1

    reply1 = events1["done"]["reply"]

    assert "Bitcoin" in reply1
    assert "$" in reply1

    history = [
        {"role": "user", "content": "bitcoin price"},
        {"role": "assistant", "content": reply1}
    ]

    res2 = client.post(
        "/api/chat",
        headers=headers,
        json={
            "prompt": "what about in INR?",
            "history": history
        }
    )

    assert res2.status_code == 200

    lines2 = [
        json.loads(line)
        for line in res2.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events2 = {line.get("event"): line for line in lines2}

    assert "done" in events2

    reply2 = events2["done"]["reply"]

    assert "Bitcoin" in reply2
    assert "₹" in reply2
    assert "Exchange Rate" in reply2


# =============================================================================
# 5. Coding Prompts & Code Generation
# =============================================================================

def test_all_coding_prompts_generation():
    slot = {
        "name": "mock_test_coding",
        "provider": "mock",
        "model": "gemini-2.5-flash",
        "api_key": ""
    }

    coding_test_cases = [
        ("can you write basic rag pipline in python ?", ["RAG", "Pipeline", "cosine_similarity", "embed"]),
        ("write binary search in python", ["binary_search", "left", "right", "O(log N)"]),
        ("write quicksort algorithm", ["quicksort", "pivot", "left", "right"]),
        ("write a rest api in flask", ["Flask", "jsonify", "app.route", "methods"]),
        ("write a web scraper in python", ["requests", "BeautifulSoup", "scrape"]),
        ("read and write csv in python", ["csv.DictReader", "csv.DictWriter", "open"]),
        ("sqlite database in python", ["sqlite3", "connect", "cursor", "execute"]),
        ("stack and queue in python", ["class Stack", "class Queue", "push", "pop"]),
        ("write a code for make 5 emepty list in python", ["[[] for _ in range(5)]", "lists"]),
        ("create a code for 10 emty set in python", ["[set() for _ in range(10)]", "set()", "empty dictionary"]),
        ("create 3 empty dict in python", ["[{} for _ in range(3)]", "dict"]),
        ("how to count vowels in a string in python", ["count_vowels", "aeiou"]),
        ("check if two strings are anagrams", ["is_anagram", "Counter"]),
        ("remove duplicates from a list in python", ["remove_duplicates", "dict.fromkeys"]),
        ("wirte coddding for calculation", ["Retry", "busy"])
    ]

    for prompt, expected_tokens in coding_test_cases:
        res = llm.generate(
            slot,
            [{"role": "user", "content": prompt}]
        )

        generated_text = res["text"]

        if "Retry" in generated_text and "busy" in generated_text:
            for token in expected_tokens:
                assert token in generated_text
        else:
            assert "```python" in generated_text

            for token in expected_tokens:
                assert token in generated_text

        assert res["latency_ms"] < 250


def test_coding_router_and_typo_tolerance():
    queries = [
        "can you write basic rag pipline in python ?",
        "wirte coddding for calculation",
        "write a python script to parse logs",
        "build a flask rest api"
    ]

    for query in queries:
        classification = router.classifier.classify(query)

        assert "coding" in classification["capabilities"]
        assert classification["tier"] in (
            "medium", "complex", "simple"
        )


def test_coding_prompt_chat_endpoint(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    res = client.post(
        "/api/chat",
        headers=headers,
        json={
            "prompt": "can you write basic rag pipline in python ?"
        }
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "done" in events

    reply = events["done"]["reply"]

    assert "```python" in reply
    assert "rag_pipeline" in reply or "RAG" in reply
    assert "cosine_similarity" in reply or "retrieve" in reply


# =============================================================================
# 6. Gemini API Key Format Validation
# =============================================================================

def test_gemini_api_key_format_validation():
    # Dummy test value: never use a real API key in tests.
    user_key = "AQ." + ("x" * 51)

    assert llm.is_valid_gemini_key(user_key) is True

    # Dummy legacy-format test value.
    legacy_key = "AIza" + ("x" * 35)

    assert llm.is_valid_gemini_key(legacy_key) is True

    # Invalid formats.
    assert llm.is_valid_gemini_key("") is False
    assert llm.is_valid_gemini_key(None) is False
    assert llm.is_valid_gemini_key("invalid_key_12345") is False
    assert llm.is_valid_gemini_key(
        "sk-proj-12345678901234567890"
    ) is False


# =============================================================================
# 7. ML Random Forest Pipeline
# =============================================================================

def test_ml_random_forest_pipeline_prompt(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    prompt = (
        "can you make some ml algorithem code take csv clean fill data "
        "with meand and midan use randome forest for model"
    )

    res = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": prompt}
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "done" in events

    reply = events["done"]["reply"]

    assert "RandomForestClassifier" in reply
    assert "SimpleImputer" in reply
    assert "mean" in reply
    assert "median" in reply
    assert "train_test_split" in reply

    assert "csv.DictReader" not in reply
    assert "test_users.csv" not in reply


# =============================================================================
# 8. Distinct Architectural Mock Prompts
# =============================================================================

def test_distinct_architectural_mock_prompts():
    slot = {
        "tier": "complex",
        "provider": "mock"
    }

    rag_prompt = (
        "Act as a Senior AI/ML Engineer. Upgrade my application into "
        "an Enterprise Multi-Tenant RAG platform with PDF ingestion "
        "and hybrid search."
    )

    rag_text, _, _ = llm._mock(
        slot,
        [{"role": "user", "content": rag_prompt}]
    )

    assert "Enterprise Multi-Tenant RAG Platform Architecture" in rag_text
    assert "Hybrid Retrieval" in rag_text
    assert "Transactional Outbox" not in rag_text

    pay_prompt = (
        "Design a distributed payment processing platform with "
        "idempotency guard, transactional outbox pattern, and Kafka."
    )

    pay_text, _, _ = llm._mock(
        slot,
        [{"role": "user", "content": pay_prompt}]
    )

    assert "Production Distributed Payment Processing Backend Architecture" in pay_text
    assert "Idempotency Guard" in pay_text
    assert "Transactional Outbox" in pay_text
    assert "Enterprise Multi-Tenant RAG" not in pay_text

    ms_prompt = (
        "Design a resilient microservice system design "
        "with circuit breaker and distributed tracing."
    )

    ms_text, _, _ = llm._mock(
        slot,
        [{"role": "user", "content": ms_prompt}]
    )

    assert "High-Scale Resilient Architecture" in ms_text


# =============================================================================
# 9. Sidebar Chat History Visibility
# =============================================================================

def test_sidebar_chat_history_visibility_on_files_and_activity(auth_client):
    client, uid = auth_client

    chat_res = client.get("/")

    assert chat_res.status_code == 200

    chat_html = chat_res.get_data(as_text=True)

    assert 'id="side-history-section"' in chat_html

    files_res = client.get("/files")

    assert files_res.status_code == 200

    files_html = files_res.get_data(as_text=True)

    assert 'id="side-history-section"' not in files_html

    act_res = client.get("/activity")

    assert act_res.status_code == 200

    act_html = act_res.get_data(as_text=True)

    assert 'id="side-history-section"' not in act_html


# =============================================================================
# 10. JavaScript Debounce Prompt
# =============================================================================

def test_javascript_debounce_prompt(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    prompt = "write a javascript function to debounce an input"

    res = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": prompt}
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "done" in events

    reply = events["done"]["reply"]

    assert "```javascript" in reply
    assert (
        "function debounce(func" in reply
        or "debounce = (func" in reply
    )
    assert "clearTimeout" in reply
    assert "setTimeout" in reply
    assert "def write_a_javascript" not in reply


# =============================================================================
# 11. SQL Window Query Prompt
# =============================================================================

def test_sql_window_query_prompt(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    prompt = (
        "write sql query to find top 5 highest paid employees "
        "per department"
    )

    res = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": prompt}
    )

    assert res.status_code == 200

    lines = [
        json.loads(line)
        for line in res.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events = {line.get("event"): line for line in lines}

    assert "done" in events

    reply = events["done"]["reply"]

    assert "```sql" in reply
    assert "DENSE_RANK()" in reply
    assert "PARTITION BY department_id" in reply
    assert "ORDER BY salary DESC" in reply
    assert "WHERE" in reply


# =============================================================================
# 12. Math Calculation
# =============================================================================

def test_math_calculation_tip_and_arithmetic(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    res1 = client.post(
        "/api/chat",
        headers=headers,
        json={
            "prompt": "calculate 15 percent tip on 450"
        }
    )

    assert res1.status_code == 200

    lines1 = [
        json.loads(line)
        for line in res1.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events1 = {line.get("event"): line for line in lines1}

    assert "done" in events1

    reply1 = events1["done"]["reply"]

    assert "67.5" in reply1
    assert "517.5" in reply1

    res2 = client.post(
        "/api/chat",
        headers=headers,
        json={"prompt": "calculate 25 * 40"}
    )

    assert res2.status_code == 200

    lines2 = [
        json.loads(line)
        for line in res2.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    events2 = {line.get("event"): line for line in lines2}

    assert "done" in events2

    reply2 = events2["done"]["reply"]

    assert "1000" in reply2


# =============================================================================
# 13. Docker Explanation & Upian Identity
# =============================================================================

def test_concept_explanation_docker_and_identity(auth_client):
    client, uid = auth_client

    headers = {
        "X-CSRF-Token": TEST_CSRF,
        "Content-Type": "application/json"
    }

    res_id = client.post(
        "/api/chat",
        headers=headers,
        json={
            "prompt": "who created you and what can you do?"
        }
    )

    assert res_id.status_code == 200

    lines_id = [
        json.loads(line)
        for line in res_id.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    reply_id = {
        line.get("event"): line for line in lines_id
    }["done"]["reply"]

    assert "Upian AI" in reply_id
    assert "Coding" in reply_id or "Deep Reasoning" in reply_id

    res_dock = client.post(
        "/api/chat",
        headers=headers,
        json={
            "prompt": "explain docker containers in simple words"
        }
    )

    assert res_dock.status_code == 200

    lines_dock = [
        json.loads(line)
        for line in res_dock.data.decode("utf-8").strip().split("\n")
        if line.strip()
    ]

    reply_dock = {
        line.get("event"): line for line in lines_dock
    }["done"]["reply"]

    assert "Docker" in reply_dock
    assert (
        "shipping container" in reply_dock
        or "Containers vs" in reply_dock
    )
