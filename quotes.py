"""REAL live quotes (no API key, nothing written by an AI model): crypto, stock indices, oil and metals,
currency exchange rates and company stocks, all from Yahoo Finance.

Same contract as marketdata.py: detect(prompt) -> spec or None, report(spec, prompt) -> {markdown, slug, source}.
If no source answers, DataUnavailable is raised and the chat tells the user honestly instead of guessing.
"""
import re
import threading
import time
from datetime import datetime, timezone

from config import Config
from marketdata import _SYMBOL, DataUnavailable, _get, fmt_local

CACHE_SECONDS = 60
_CACHE, _LOCK = {}, threading.Lock()

_NOT_DATA = re.compile(r"\b(why|how (?:do|does|is|are|can)|explain|reason|affect\w*|determin\w*|predict\w*|forecast\w*|invest\w*|should i)\b", re.I)
_ASK = re.compile(r"\b(price|prices|rate|rates|value|worth|quote|trading|current\w*|today|now|live|level|close|closing|how much|stock|share)\b", re.I)

# order matters: more specific phrases first
NAMED = [
    (r"bank\s*nifty", "^NSEBANK", "Bank Nifty", "points"),
    (r"nifty(?:\s*50)?", "^NSEI", "Nifty 50", "points"),
    (r"sensex", "^BSESN", "BSE Sensex", "points"),
    (r"s&p\s*500|s&p|sp500", "^GSPC", "S&P 500", "points"),
    (r"nasdaq", "^IXIC", "Nasdaq Composite", "points"),
    (r"dow\s*jones|dow", "^DJI", "Dow Jones", "points"),
    (r"bitcoin|btc", "BTC-USD", "Bitcoin", "USD"),
    (r"ethereum|ether|eth", "ETH-USD", "Ethereum", "USD"),
    (r"dogecoin|doge", "DOGE-USD", "Dogecoin", "USD"),
    (r"ripple|xrp", "XRP-USD", "XRP", "USD"),
    (r"brent", "BZ=F", "Brent crude oil", "USD per barrel"),
    (r"crude(?:\s+oil)?|wti|oil\s+price|price\s+of\s+oil", "CL=F", "WTI crude oil", "USD per barrel"),
    (r"natural\s+gas", "NG=F", "Natural gas", "USD per MMBtu"),
    (r"copper", "HG=F", "Copper", "USD per pound"),
    (r"platinum", "PL=F", "Platinum", "USD per troy ounce"),
    (r"palladium", "PA=F", "Palladium", "USD per troy ounce"),
]

_CODES = {"USD", "INR", "EUR", "GBP", "AED", "SAR", "JPY", "CAD", "AUD", "SGD", "CNY", "PKR", "CHF", "NZD", "ZAR"}
_NAMES = {"dollar": "USD", "dollars": "USD", "rupee": "INR", "rupees": "INR", "euro": "EUR", "euros": "EUR",
          "pound": "GBP", "pounds": "GBP", "dirham": "AED", "dirhams": "AED", "riyal": "SAR", "yen": "JPY", "yuan": "CNY"}
_FX_ASK = re.compile(r"\b(rate|rates|exchange|convert\w*|worth|value|price|today|now|current\w*|live|how much|to)\b|/", re.I)

_STOCK_AFTER = re.compile(r"(?:stock|share)\s+price\s+(?:of\s+|for\s+)?([a-z0-9&.\- ]{2,40})", re.I)
_STOCK_BEFORE = re.compile(r"([a-z0-9&.\- ]{2,40}?)\s+(?:stock|share)\s+price", re.I)
_FILLER = re.compile(r"\b(what|whats|what's|is|the|a|an|of|for|me|give|show|tell|today|now|current\w*|live|right|please|price|in|inr|usd|india|us)\b", re.I)


def _find_named(prompt):
    for pat, symbol, label, unit in NAMED:
        if re.search(r"(?<!\w)(?:%s)(?!\w)" % pat, prompt, re.I):
            return symbol, label, unit
    return None


def _currencies_in(prompt):
    found = []
    for m in re.finditer(r"[A-Za-z]+", prompt):
        w = m.group(0)
        code = _NAMES.get(w.lower()) or (w.upper() if len(w) == 3 and w.upper() in _CODES else None)
        if code and code not in found:
            found.append(code)
    return found


def _stock_name(prompt):
    m = _STOCK_AFTER.search(prompt) or _STOCK_BEFORE.search(prompt)
    if not m:
        return None
    name = re.sub(r"\s+", " ", _FILLER.sub(" ", m.group(1))).strip(" .-")
    return name if len(name) >= 2 else None


def detect(prompt):
    """-> {'kind': 'named'|'fx'|'stock', ...} when the prompt asks for a live price this module can fetch, else None."""
    if _NOT_DATA.search(prompt) or re.search(r"\b(gold|silver|weather)\b", prompt, re.I):
        return None
    ccy = _currencies_in(prompt)
    if len(ccy) >= 2 and _FX_ASK.search(prompt):
        return {"kind": "fx", "base": ccy[0], "quote": ccy[1]}
    named = _find_named(prompt)
    if named and _ASK.search(prompt):
        return {"kind": "named", "symbol": named[0], "label": named[1], "unit": named[2]}
    name = _stock_name(prompt)
    if name:
        return {"kind": "stock", "name": name}
    return None


# ----------------------------------------------------------------------------- fetching
def fetch_quote(symbol):
    """-> dict(price, prev, when, currency, name). Cached for a minute."""
    key = ("q", symbol)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
    try:
        data = _get(url, {"range": "1d", "interval": "1d"}).json()
        meta = data["chart"]["result"][0]["meta"]
        out = {"price": float(meta["regularMarketPrice"]),
               "prev": float(meta["chartPreviousClose"]) if meta.get("chartPreviousClose") else None,
               "when": datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc),
               "currency": meta.get("currency") or "",
               "name": meta.get("shortName") or meta.get("longName") or symbol}
    except (KeyError, IndexError, TypeError, ValueError):
        raise DataUnavailable("no data for %s" % symbol)
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def search_symbol(name):
    """Company name -> Yahoo symbol (for example 'reliance' -> RELIANCE.NS)."""
    data = _get("https://query1.finance.yahoo.com/v1/finance/search",
                {"q": name, "quotesCount": 6, "newsCount": 0}).json()
    for q in data.get("quotes") or []:
        if q.get("quoteType") in ("EQUITY", "ETF") and q.get("symbol"):
            return q["symbol"]
    raise DataUnavailable("no stock found for '%s'" % name)


def _local_time(dt):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(Config.APP_TIMEZONE)
    except Exception:
        tz = timezone.utc
    return dt.astimezone(tz).strftime("%d %b %Y, %I:%M %p %Z").replace(" 0", " ", 1)


def _num(v):
    return "{:,.4f}".format(v) if abs(v) < 1 else "{:,.2f}".format(v)


def _change(q):
    if not q["prev"]:
        return "n/a"
    return "%+.2f%%" % ((q["price"] / q["prev"] - 1) * 100)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "quote"


# ----------------------------------------------------------------------------- answers
def _report_quote(symbol, label, unit, prompt):
    q = fetch_quote(symbol)
    unit = unit if unit != "USD" else (q["currency"] or "USD")
    price = _num(q["price"])
    md = ("### %s price now (%s)\n\n| Time | Price (%s) | Change vs previous close |\n|---|---|---|\n| %s | %s | %s |\n"
          % (label, unit, unit, _local_time(q["when"]), price, _change(q)))
    return {"markdown": md, "slug": "%s-price-latest" % _slug(label), "source": ("%s, Yahoo Finance" % label)[:80]}


def _fx_rate(base, quote):
    """Units of `quote` per 1 `base`, and the time of the rate."""
    if base == quote:
        return 1.0, datetime.now(timezone.utc)
    if base == "USD":
        q = fetch_quote("%s=X" % quote)
        return q["price"], q["when"]
    if quote == "USD":
        q = fetch_quote("%s=X" % base)
        return 1.0 / q["price"], q["when"]
    q = fetch_quote("%s%s=X" % (base, quote))
    return q["price"], q["when"]


def _report_fx(base, quote, prompt):
    rate, when = _fx_rate(base, quote)
    alts = "|".join([base] + [n for n, c in _NAMES.items() if c == base])
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:%s)\b" % alts, prompt, re.I)
    if not m and base == "USD":
        m = re.search(r"\$\s*(\d[\d,]*(?:\.\d+)?)", prompt)
    amount = float(m.group(1).replace(",", "")) if m else None
    rows = "| 1 %s | %s%s |\n" % (base, _SYMBOL.get(quote, quote + " "), "{:,.4f}".format(rate))
    if amount and amount != 1:
        rows += "| %s %s | %s |\n" % ("{:,.2f}".format(amount).rstrip("0").rstrip("."), base, fmt_local(amount * rate, quote))
    md = ("### %s to %s exchange rate now\n\n| Amount | Value in %s |\n|---|---|\n%s\n"
          % (base, quote, quote, rows))
    return {"markdown": md, "slug": "%s-to-%s-rate" % (base.lower(), quote.lower()), "source": "%s/%s, Yahoo Finance" % (base, quote)}


def report(spec, prompt):
    if spec["kind"] == "fx":
        return _report_fx(spec["base"], spec["quote"], prompt)
    if spec["kind"] == "named":
        return _report_quote(spec["symbol"], spec["label"], spec["unit"], prompt)
    symbol = search_symbol(spec["name"])
    q = fetch_quote(symbol)
    return _report_quote(symbol, q["name"], q["currency"] or "USD", prompt)
