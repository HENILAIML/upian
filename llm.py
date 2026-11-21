"""Provider calls (Gemini, OpenAI-compatible, Anthropic) and the fallback chain."""
import time
from datetime import datetime, timezone

import requests

from config import SLOT_ORDER, SLOTS, Config

BASE_PROMPT = (
    "You are Upian AI, an intelligent, helpful, and friendly AI assistant. "
    "Your mission is to make every answer exceptionally clear, engaging, well-structured, and easy for any user to understand.\n\n"
    "Presentation & Formatting Rules:\n"
    "• Clarity & Simplicity: Explain concepts in plain, natural language. Use simple real-world analogies or examples to make complex or technical ideas instantly understandable.\n"
    "• Clean Structure: Avoid dense walls of text. Break responses into short, readable paragraphs. Use markdown headings (###) for major sections.\n"
    "• Highlighting: Use bold text (**important terms and key takeaways**) so the reader can easily scan and absorb the main points.\n"
    "• Bullet Points & Steps: Use clean bullet points for lists, features, or reasons, and numbered steps (1., 2., 3.) for instructions or workflows.\n"
    "• Tables for Comparisons & Data: When comparing options, showing stats, numbers, or prices, always organize them in a clean Markdown table with descriptive headers.\n"
    "• Code & Technical Terms: Always enclose code in fenced code blocks with the language identifier (e.g. ```python). Enclose commands and filenames in inline code (`term`).\n"
    "• Tone: Warm, helpful, professional, and straight to the point."
)




def system_prompt():
    """The model has no clock and no internet, so tell it the real date and forbid inventing live data."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(Config.APP_TIMEZONE))
    except Exception:
        now = datetime.now(timezone.utc)
    return (
        BASE_PROMPT
        + "\n\nContext & Date Rules:\n"
        + "Today is %s (%s). " % (now.strftime("%A, %d %B %Y, %I:%M %p"), Config.APP_TIMEZONE)
        + "You have NO internet access and NO live data. Never invent weather, news, scores, exchange rates or prices "
          "for specific dates, and never give data for dates after today. If the user asks for such live data, say you "
          "do not have it and tell them where to check; do not make up numbers. "
          "For general figures you know (for example typical car prices), say they are approximate and may be outdated. "
          "When giving figures or comparisons, use a clean markdown table with a header row, consistent units in the header, and a short title."
    )

class ProviderError(Exception):
    pass


def _clean(text, slot):
    text = str(text)
    if slot.get("api_key"):
        text = text.replace(slot["api_key"], "***")
    return text[:300]


_FAILED = {}      # slot name -> time of last failure
COOLDOWN = 60     # seconds to skip a failed slot


def build_chain(tier="simple"):
    """Tier-aware provider chain with intelligent model routing.
    Supports 'simple', 'medium', 'complex', and legacy 'weak'/'strong' tiers."""
    tier = str(tier).lower()
    chain, seen = [], set()

    def add_slot(slot):
        if not slot:
            return
        sig = (slot["provider"], slot["api_key"], slot["model"], slot["base_url"])
        if sig not in seen:
            seen.add(sig)
            chain.append(slot)

    gemini_key = SLOTS.get("free", {}).get("api_key")
    gemini_base = SLOTS.get("free", {}).get("base_url")

    if tier in ("complex", "strong"):
        for name in ("strong_1", "strong_2"):
            add_slot(SLOTS.get(name))
        # Add Gemini complex model if key is available
        if gemini_key:
            add_slot({
                "name": "gemini_complex",
                "provider": "gemini",
                "api_key": gemini_key,
                "model": getattr(Config, "GEMINI_COMPLEX_MODEL", "gemini-3.8-pro"),
                "base_url": gemini_base,
            })
            add_slot({
                "name": "gemini_medium",
                "provider": "gemini",
                "api_key": gemini_key,
                "model": getattr(Config, "GEMINI_MEDIUM_MODEL", "gemini-3.8-flash"),
                "base_url": gemini_base,
            })
        add_slot(SLOTS.get("free"))

    elif tier == "medium":
        for name in ("weak_1", "weak_2"):
            add_slot(SLOTS.get(name))
        if gemini_key:
            add_slot({
                "name": "gemini_medium",
                "provider": "gemini",
                "api_key": gemini_key,
                "model": getattr(Config, "GEMINI_MEDIUM_MODEL", "gemini-3.8-flash"),
                "base_url": gemini_base,
            })
        add_slot(SLOTS.get("free"))

    else:  # simple / weak
        add_slot(SLOTS.get("free"))
        for name in ("weak_1", "weak_2"):
            add_slot(SLOTS.get(name))

    now = time.time()
    healthy = [s for s in chain if now - _FAILED.get(s["name"], 0) > COOLDOWN]
    return healthy or chain


def slots_status():
    out = []
    for name in SLOT_ORDER:
        slot = SLOTS.get(name)
        out.append(
            {
                "name": name,
                "configured": bool(slot),
                "provider": slot["provider"] if slot else "",
                "model": slot["model"] if slot else "",
                "key_hint": ("..." + slot["api_key"][-4:]) if slot else "",
            }
        )
    return out


def _post(slot, url, headers, body):
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=(5, Config.LLM_TIMEOUT))
    except (requests.RequestException, OSError) as exc:
        raise ProviderError("network error: " + _clean(exc, slot))
    if resp.status_code >= 400:
        raise ProviderError("HTTP %s %s" % (resp.status_code, _clean(resp.text, slot)))
    try:
        return resp.json()
    except ValueError:
        raise ProviderError("provider returned invalid JSON")


def _gemini(slot, messages):
    base = (slot["base_url"] or "https://generativelanguage.googleapis.com").rstrip("/")
    model = slot["model"].replace("models/", "", 1)
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt()}]},
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ],
        "generationConfig": {"maxOutputTokens": Config.MAX_OUTPUT_TOKENS, "temperature": 0.7},
    }
    headers = {"x-goog-api-key": slot["api_key"], "Content-Type": "application/json"}
    data = _post(slot, "%s/v1beta/models/%s:generateContent" % (base, model), headers, body)
    cands = data.get("candidates") or []
    text = ""
    if cands:
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if not text.strip():
        reason = (cands[0].get("finishReason") if cands else None) or (data.get("promptFeedback") or {}).get("blockReason") or "no text"
        raise ProviderError("empty response (%s)" % reason)
    usage = data.get("usageMetadata") or {}
    tokens_in = int(usage.get("promptTokenCount") or 0)
    total = int(usage.get("totalTokenCount") or 0)
    tokens_out = max(total - tokens_in, 0) if total else int(usage.get("candidatesTokenCount") or 0)
    return text.strip(), tokens_in, tokens_out


def _openai(slot, messages):
    base = (slot["base_url"] or "https://api.openai.com/v1").rstrip("/")
    limit_key = "max_completion_tokens" if "api.openai.com" in base else "max_tokens"
    body = {
        "model": slot["model"],
        "messages": [{"role": "system", "content": system_prompt()}] + messages,
        limit_key: Config.MAX_OUTPUT_TOKENS,
    }
    headers = {"Authorization": "Bearer " + slot["api_key"], "Content-Type": "application/json"}
    data = _post(slot, base + "/chat/completions", headers, body)
    choices = data.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
    if not text.strip():
        raise ProviderError("empty response")
    usage = data.get("usage") or {}
    return text.strip(), int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _anthropic(slot, messages):
    base = (slot["base_url"] or "https://api.anthropic.com").rstrip("/")
    body = {
        "model": slot["model"],
        "max_tokens": Config.MAX_OUTPUT_TOKENS,
        "system": system_prompt(),
        "messages": messages,
    }
    headers = {"x-api-key": slot["api_key"], "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    data = _post(slot, base + "/v1/messages", headers, body)
    text = "".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text")
    if not text.strip():
        raise ProviderError("empty response")
    usage = data.get("usage") or {}
    return text.strip(), int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


_PROVIDERS = {"gemini": _gemini, "openai": _openai, "anthropic": _anthropic}


def generate(slot, messages):
    fn = _PROVIDERS.get(slot["provider"])
    if not fn:
        raise ProviderError("unknown provider '%s'" % slot["provider"])
    started = time.perf_counter()
    try:
        text, tokens_in, tokens_out = fn(slot, messages)
    except ProviderError:
        _FAILED[slot["name"]] = time.time()
        raise
    return {
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
