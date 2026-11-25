"""Tests for Gujarati and Gujlish (Romanized Gujarati) multilingual prompt support.
Verifies that:
1. Router detects coding capability and appropriate tier for Gujarati prompts without crashing.
2. Casual Gujarati greetings (e.g. 'kem cho bhai', 'કેમ છો') are handled instantly.
3. Code generation produces valid Python code for 'bhai 1 to 10 prinet thai avo code lakho python ma'.
4. Chat endpoint executes end-to-end with 200 OK and valid stream events.
"""
import pytest
import json
from app import app
from models import db, User
import router
import llm

TEST_CSRF = "test_csrf_token_gujarati_12345"


@pytest.fixture
def auth_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with app.app_context():
            u = User.query.filter_by(email="gujarati_tester@upian.local").first()
            if not u:
                u = User(name="Gujarati Tester", email="gujarati_tester@upian.local", plan="free")
                u.set_password("Secret123!")
                db.session.add(u)
                db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["uid"] = uid
            sess["csrf"] = TEST_CSRF
        yield client, uid


def test_gujarati_routing_no_crash():
    """Verify router handles Gujlish and native Gujarati script prompts."""
    prompts = [
        "bhai 1 to 10 prinet thai avo code lakho python ma",
        "૧ થી ૧૦ પ્રિન્ટ થાય એવો કોડ લખો પાયથનમાં",
        "python ma loop kevi rite banavvi",
        "kem cho bhai",
    ]
    for p in prompts:
        res = router.classifier.classify(p)
        assert isinstance(res, dict)
        assert "tier" in res
        assert "capabilities" in res
        if "1 to 10" in p or "પ્રિન્ટ" in p or "loop" in p:
            assert "coding" in res["capabilities"]


def test_gujarati_greetings():
    """Verify instant greetings work for Gujarati pleasantries."""
    for phrase in ["kem cho", "kem cho bhai", "bhai kem cho", "kem chho", "કેમ છો"]:
        is_greeting, reply = llm.is_casual_greeting(phrase)
        assert is_greeting is True
        assert len(reply) > 0


def test_gujarati_mock_code_generation():
    """Verify mock provider generates Python code and Gujarati explanation."""
    slot = {"name": "test_mock", "provider": "mock", "model": "gemini-3.8-flash", "api_key": ""}
    res = llm.generate(slot, [{"role": "user", "content": "bhai 1 to 10 prinet thai avo code lakho python ma"}])
    text = res["text"]
    assert "for i in range" in text
    assert "print(i)" in text
    assert "```python" in text


def test_gujarati_chat_endpoint_e2e(auth_client):
    """Verify /api/chat end-to-end streaming response for Gujarati code prompt."""
    client, uid = auth_client
    headers = {"X-CSRFToken": TEST_CSRF, "Content-Type": "application/json"}
    payload = {
        "prompt": "bhai 1 to 10 prinet thai avo code lakho python ma",
        "history": []
    }
    res = client.post("/api/chat", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.get_data(as_text=True)

    # Must contain stream events without error
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    event_names = [e.get("event") for e in events]
    assert "route" in event_names
    assert "done" in event_names
    assert not any(e.get("event") == "error" for e in events)

    done_event = next(e for e in events if e.get("event") == "done")
    reply = done_event.get("reply", "")
    assert "for i in range" in reply or "print" in reply


def test_diverse_new_gujarati_words():
    """Verify system handles any new Gujarati words and general non-coding questions."""
    slot = {"name": "test_mock", "provider": "mock", "model": "gemini-3.8-flash", "api_key": ""}
    new_gujarati_prompts = [
        "mane surat vishe mahiti aapo",
        "navo business sharu karva mate su dhyan rakhvu joye?",
        "મારે શેરબજારમાં રોકાણ કરવું છે કેવી રીતે કરું?",
        "aaje havaman kevu raheshe?",
        "તમે કઈ કઈ ભાષાઓ જાણો છો?",
    ]
    for prompt in new_gujarati_prompts:
        # Router must classify smoothly without error
        r = router.classifier.classify(prompt)
        assert "tier" in r
        assert "capabilities" in r

        # Generator must handle without crashing
        res = llm.generate(slot, [{"role": "user", "content": prompt}])
        text = res["text"]
        assert len(text) > 20
        assert "Upian AI" in text or "Python" in text or "નમસ્તે" in text or "પ્રશ્ન" in text or "હવામાન" in text or "Weather" in text


def test_realtime_gujarati_weather_and_web_access():
    """Verify real-time live weather and web search detection and execution."""
    import weather
    import websearch
    import special

    # 1. Weather detection in Gujarati & Gujlish
    assert weather.detect("aaje havaman kevu raheshe?") is True
    assert weather.detect("Surat ma havaman kevu chhe?") is True
    assert weather.detect("અમદાવાદનું હવામાન કેવું રહેશે?") is True
    assert weather.detect("આજે વરસાદ પડશે કે નહીં?") is True

    # 2. City extraction
    city, is_def = weather.extract_city("સુરતમાં હવામાન કેવું છે?")
    assert city == "Surat"
    assert is_def is False

    city2, is_def2 = weather.extract_city("અમદાવાદનું આજનું તાપમાન")
    assert city2 == "Ahmedabad"
    assert is_def2 is False

    city3, is_def3 = weather.extract_city("aaje havaman kevu raheshe?")
    assert city3 == "Surat"
    assert is_def3 is True

    # 3. Web search detection
    assert websearch.detect("search the web for artificial intelligence") is True
    assert websearch.detect("ઈન્ટરનેટ પર શોધો Python") is True

    # 4. Special handler routing
    sp = special.handle(1, "aaje havaman kevu raheshe?", [])
    assert sp is not None
    assert sp["source"] == "Open-Meteo weather"
    assert "હવામાન" in sp["reply"] or "Weather" in sp["reply"]
    assert "સુરત" in sp["reply"] or "Surat" in sp["reply"]


def test_gujarati_pdf_and_file_export():
    """Verify PDF export correctly embeds Gujarati characters without tofu/boxes."""
    import io
    import pypdf
    import filetools

    content = (
        "# ઉબકા, પેટ ખરાબ (Upset Stomach) માટે ઘરેલું ઉપાયો\n\n"
        "અહીં કેટલીક મહત્વપૂર્ણ બાબતો છે:\n\n"
        "- આદુનો રસ પીવો\n"
        "- લીંબુ પાણી પીવો\n"
        "- વરિયાળી ચાવો\n\n"
        "| ઉપાય | સમય | ફાયદો |\n"
        "| --- | --- | --- |\n"
        "| આદુ ચા | સવારે | પાચન સુધારે |\n"
        "| જીરું પાણી | બપોરે | ગેસ ઓછો કરે |\n"
    )

    pdf_bytes, mime, name = filetools.make_file(content, "pdf")
    assert mime == "application/pdf"
    assert len(pdf_bytes) > 1000

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    extracted = "".join(page.extract_text() for page in reader.pages)
    assert "ઉબકા" in extracted
    assert "પેટ ખરાબ" in extracted
    assert "આદુનો રસ" in extracted
    assert "જીરું પાણી" in extracted


