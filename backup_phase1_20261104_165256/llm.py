"""Provider calls (Gemini, OpenAI-compatible, Anthropic) and the fallback chain."""
import time
from datetime import datetime, timezone

import requests

from config import SLOT_ORDER, SLOTS, Config

BASE_PROMPT = (
    "You are Upian AI, a helpful assistant. Answer clearly and accurately. "
    "Keep simple answers short. For code, use fenced code blocks. "
    "When the user asks for prices, comparisons, lists of figures or statistics, put the key data in a markdown table "
    "(clear column headers, units in the headers) and keep any extra text short. "
    "If you are not sure about a number, say it is approximate and that it should be checked."
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
        + " Today is %s (%s). " % (now.strftime("%A, %d %B %Y, %I:%M %p"), Config.APP_TIMEZONE)
        + "You have NO internet access and NO live data. Never invent weather, news, scores, exchange rates or prices "
          "for specific dates, and never give data for dates after today. If the user asks for such live data, say you "
          "do not have it and tell them where to check; do not make up numbers. "
          "For general figures you know (for example typical car prices), say they are approximate and may be outdated. "
          "Formatting: when giving figures or comparisons use a clean markdown table with a header row, one fact per cell, "
          "units only in the header (write 32, not 32°C), consistent decimals, no emoji, and a short title above it."
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


def build_chain(tier):
    """Strong prompts try strong slots first, weak prompts try weak slots first.
    The free Gemini key is always the last resort."""
    names = ["strong_1", "strong_2"] if tier == "strong" else ["weak_1", "weak_2"]
    names.append("free")
    chain, seen = [], set()
    for name in names:
        slot = SLOTS.get(name)
        if not slot:
            continue
        sig = (slot["provider"], slot["api_key"], slot["model"], slot["base_url"])
        if sig in seen:
            continue
        seen.add(sig)
        chain.append(slot)
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
    except requests.RequestException as exc:
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
