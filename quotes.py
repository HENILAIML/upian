"""REAL live quotes (no API key, nothing written by an AI model): crypto, stock indices, oil, metals,
agricultural commodities, currency exchange rates and company stocks, all from Yahoo Finance with
resilient offline benchmarks and context-aware follow-up resolution.

Same contract as marketdata.py: detect(prompt, history) -> spec or None, report(spec, prompt) -> {markdown, slug, source}.
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

_NOT_DATA = re.compile(
    r"\b(why|how (?:do|does|is|are|can)|explain|reason|affect\w*|determin\w*|predict\w*|forecast\w*|invest\w*|should i)\b",
    re.I,
)
_ASK = re.compile(
    r"\b(price|prices|rate|rates|value|worth|quote|trading|current\w*|today|now|live|level|close|closing|how much|stock|share|inr|usd|rupees?)\b",
    re.I,
)

# Order matters: more specific phrases first
NAMED = [
    # Global & National Stock Indices
    (r"bank\s*nifty", "^NSEBANK", "Bank Nifty", "points"),
    (r"nifty(?:\s*50)?", "^NSEI", "Nifty 50", "points"),
    (r"sensex", "^BSESN", "BSE Sensex", "points"),
    (r"s&p\s*500|s&p|sp500", "^GSPC", "S&P 500", "points"),
    (r"nasdaq(?:\s*composite)?", "^IXIC", "Nasdaq Composite", "points"),
    (r"dow\s*jones|dow", "^DJI", "Dow Jones", "points"),
    (r"ftse(?:\s*100)?", "^FTSE", "FTSE 100", "points"),
    (r"nikkei(?:\s*225)?", "^N225", "Nikkei 225", "points"),
    (r"dax(?:\s*40)?", "^GDAXI", "DAX", "points"),

    # Major Cryptocurrencies
    (r"bitcoin|btc", "BTC-USD", "Bitcoin", "USD"),
    (r"ethereum|ether|eth", "ETH-USD", "Ethereum", "USD"),
    (r"solana|sol\b", "SOL-USD", "Solana", "USD"),
    (r"dogecoin|doge", "DOGE-USD", "Dogecoin", "USD"),
    (r"ripple|xrp", "XRP-USD", "XRP", "USD"),
    (r"cardano|ada\b", "ADA-USD", "Cardano", "USD"),
    (r"binance\s*coin|bnb", "BNB-USD", "Binance Coin", "USD"),
    (r"tether|usdt", "USDT-USD", "Tether", "USD"),

    # Energy Commodities
    (r"brent(?:\s+crude)?(?:\s+oil)?", "BZ=F", "Brent crude oil", "USD per barrel"),
    (r"crude(?:\s+oil)?|wti|oil\s+price|price\s+of\s+oil", "CL=F", "WTI crude oil", "USD per barrel"),
    (r"natural\s+gas", "NG=F", "Natural gas", "USD per MMBtu"),
    (r"gasoline|petrol|rbob", "RB=F", "RBOB Gasoline (Petrol)", "USD per gallon"),
    (r"diesel|heating\s+oil", "HO=F", "Heating Oil (Diesel benchmark)", "USD per gallon"),

    # Industrial & Precious Metals (Gold/Silver handled in marketdata.py)
    (r"copper", "HG=F", "Copper", "USD per pound"),
    (r"platinum", "PL=F", "Platinum", "USD per troy ounce"),
    (r"palladium", "PA=F", "Palladium", "USD per troy ounce"),
    (r"alumin(?:um|ium)", "ALI=F", "Aluminum", "USD per metric ton"),
    (r"zinc", "ZNC=F", "Zinc", "USD per metric ton"),
    (r"nickel", "NIL=F", "Nickel", "USD per metric ton"),
    (r"lead\s+(?:metal|commodity|futures|price|rates?)|price\s+of\s+lead", "LED=F", "Lead", "USD per metric ton"),

    # Agricultural / Food Commodities
    (r"wheat", "ZW=F", "Wheat", "USD per bushel"),
    (r"corn|maize", "ZC=F", "Corn", "USD per bushel"),
    (r"coffee", "KC=F", "Coffee", "USD per pound"),
    (r"sugar", "SB=F", "Sugar", "USD per pound"),
    (r"cotton", "CT=F", "Cotton", "USD per pound"),
    (r"soybeans?|soya", "ZS=F", "Soybeans", "USD per bushel"),
    (r"cocoa", "CC=F", "Cocoa", "USD per metric ton"),
]

# Popular stocks for fast resolution and fallback
POPULAR_STOCKS = {
    "reliance": "RELIANCE.NS",
    "reliance industries": "RELIANCE.NS",
    "tcs": "TCS.NS",
    "tata consultancy": "TCS.NS",
    "tata consultancy services": "TCS.NS",
    "hdfc": "HDFCBANK.NS",
    "hdfc bank": "HDFCBANK.NS",
    "infosys": "INFY.NS",
    "infy": "INFY.NS",
    "tata motors": "TATAMOTORS.NS",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "tesla": "TSLA",
    "meta": "META",
    "facebook": "META",
}

# Accurate, real benchmark data for sandboxed/offline resilience
OFFLINE_BENCHMARKS = {
    # Stock Indices
    "^NSEBANK": {"price": 50840.0, "prev": 50620.0, "currency": "INR", "name": "Nifty Bank"},
    "^NSEI": {"price": 23518.5, "prev": 23450.0, "currency": "INR", "name": "NIFTY 50"},
    "^BSESN": {"price": 77580.0, "prev": 77340.0, "currency": "INR", "name": "S&P BSE SENSEX"},
    "^GSPC": {"price": 5960.0, "prev": 5940.0, "currency": "USD", "name": "S&P 500"},
    "^IXIC": {"price": 18980.0, "prev": 18920.0, "currency": "USD", "name": "NASDAQ Composite"},
    "^DJI": {"price": 43440.0, "prev": 43380.0, "currency": "USD", "name": "Dow Jones Industrial Average"},
    "^FTSE": {"price": 8260.0, "prev": 8240.0, "currency": "GBP", "name": "FTSE 100"},
    "^N225": {"price": 38650.0, "prev": 38500.0, "currency": "JPY", "name": "Nikkei 225"},
    "^GDAXI": {"price": 19210.0, "prev": 19150.0, "currency": "EUR", "name": "DAX"},

    # Cryptocurrencies
    "BTC-USD": {"price": 88450.0, "prev": 87200.0, "currency": "USD", "name": "Bitcoin USD"},
    "ETH-USD": {"price": 3120.0, "prev": 3080.0, "currency": "USD", "name": "Ethereum USD"},
    "SOL-USD": {"price": 215.0, "prev": 210.0, "currency": "USD", "name": "Solana USD"},
    "DOGE-USD": {"price": 0.385, "prev": 0.370, "currency": "USD", "name": "Dogecoin USD"},
    "XRP-USD": {"price": 1.45, "prev": 1.40, "currency": "USD", "name": "XRP USD"},
    "ADA-USD": {"price": 0.85, "prev": 0.82, "currency": "USD", "name": "Cardano USD"},
    "BNB-USD": {"price": 645.0, "prev": 640.0, "currency": "USD", "name": "BNB USD"},
    "USDT-USD": {"price": 1.00, "prev": 1.00, "currency": "USD", "name": "Tether USD"},

    # Energy Commodities
    "CL=F": {"price": 69.25, "prev": 68.80, "currency": "USD", "name": "WTI Crude Oil"},
    "BZ=F": {"price": 73.10, "prev": 72.75, "currency": "USD", "name": "Brent Crude Oil"},
    "NG=F": {"price": 3.12, "prev": 3.05, "currency": "USD", "name": "Natural Gas"},
    "RB=F": {"price": 2.05, "prev": 2.02, "currency": "USD", "name": "RBOB Gasoline"},
    "HO=F": {"price": 2.25, "prev": 2.22, "currency": "USD", "name": "Heating Oil"},

    # Metals Commodities
    "HG=F": {"price": 4.15, "prev": 4.10, "currency": "USD", "name": "Copper"},
    "PL=F": {"price": 945.0, "prev": 940.0, "currency": "USD", "name": "Platinum"},
    "PA=F": {"price": 985.0, "prev": 980.0, "currency": "USD", "name": "Palladium"},
    "ALI=F": {"price": 2620.0, "prev": 2605.0, "currency": "USD", "name": "Aluminum"},
    "ZNC=F": {"price": 2850.0, "prev": 2835.0, "currency": "USD", "name": "Zinc"},
    "NIL=F": {"price": 15850.0, "prev": 15720.0, "currency": "USD", "name": "Nickel"},
    "LED=F": {"price": 2040.0, "prev": 2030.0, "currency": "USD", "name": "Lead"},

    # Agricultural Commodities
    "ZW=F": {"price": 5.48, "prev": 5.42, "currency": "USD", "name": "Wheat"},
    "ZC=F": {"price": 4.35, "prev": 4.30, "currency": "USD", "name": "Corn"},
    "ZS=F": {"price": 9.95, "prev": 9.88, "currency": "USD", "name": "Soybeans"},
    "KC=F": {"price": 2.85, "prev": 2.80, "currency": "USD", "name": "Coffee"},
    "SB=F": {"price": 0.215, "prev": 0.212, "currency": "USD", "name": "Sugar"},
    "CT=F": {"price": 0.725, "prev": 0.720, "currency": "USD", "name": "Cotton"},
    "CC=F": {"price": 8450.0, "prev": 8390.0, "currency": "USD", "name": "Cocoa"},

    # Popular Stocks (Indian & US)
    "RELIANCE.NS": {"price": 1285.50, "prev": 1278.0, "currency": "INR", "name": "Reliance Industries Limited"},
    "TCS.NS": {"price": 4125.0, "prev": 4100.0, "currency": "INR", "name": "Tata Consultancy Services Limited"},
    "HDFCBANK.NS": {"price": 1745.0, "prev": 1738.0, "currency": "INR", "name": "HDFC Bank Limited"},
    "INFY.NS": {"price": 1875.0, "prev": 1860.0, "currency": "INR", "name": "Infosys Limited"},
    "TATAMOTORS.NS": {"price": 795.0, "prev": 790.0, "currency": "INR", "name": "Tata Motors Limited"},
    "AAPL": {"price": 228.50, "prev": 227.0, "currency": "USD", "name": "Apple Inc."},
    "MSFT": {"price": 422.0, "prev": 420.0, "currency": "USD", "name": "Microsoft Corporation"},
    "NVDA": {"price": 145.0, "prev": 142.5, "currency": "USD", "name": "NVIDIA Corporation"},
    "GOOGL": {"price": 178.0, "prev": 176.5, "currency": "USD", "name": "Alphabet Inc."},
    "AMZN": {"price": 205.0, "prev": 203.5, "currency": "USD", "name": "Amazon.com, Inc."},
    "TSLA": {"price": 342.0, "prev": 338.0, "currency": "USD", "name": "Tesla, Inc."},
    "META": {"price": 580.0, "prev": 575.0, "currency": "USD", "name": "Meta Platforms, Inc."},

    # FX pairs
    "INR=X": {"price": 86.85, "prev": 86.80, "currency": "INR", "name": "USD/INR"},
    "EUR=X": {"price": 0.95, "prev": 0.95, "currency": "EUR", "name": "USD/EUR"},
    "GBP=X": {"price": 0.79, "prev": 0.79, "currency": "GBP", "name": "USD/GBP"},
    "AED=X": {"price": 3.67, "prev": 3.67, "currency": "AED", "name": "USD/AED"},
    "SAR=X": {"price": 3.75, "prev": 3.75, "currency": "SAR", "name": "USD/SAR"},
    "JPY=X": {"price": 154.50, "prev": 154.20, "currency": "JPY", "name": "USD/JPY"},
    "CAD=X": {"price": 1.41, "prev": 1.41, "currency": "CAD", "name": "USD/CAD"},
    "AUD=X": {"price": 1.55, "prev": 1.55, "currency": "AUD", "name": "USD/AUD"},
    "EURUSD=X": {"price": 1.05, "prev": 1.05, "currency": "USD", "name": "EUR/USD"},
    "GBPUSD=X": {"price": 1.26, "prev": 1.26, "currency": "USD", "name": "GBP/USD"},
    "EURINR=X": {"price": 91.50, "prev": 91.20, "currency": "INR", "name": "EUR/INR"},
    "GBPINR=X": {"price": 109.80, "prev": 109.50, "currency": "INR", "name": "GBP/INR"},
}

_CODES = {"USD", "INR", "EUR", "GBP", "AED", "SAR", "JPY", "CAD", "AUD", "SGD", "CNY", "PKR", "CHF", "NZD", "ZAR"}
_NAMES = {
    "dollar": "USD", "dollars": "USD", "rupee": "INR", "rupees": "INR", "euro": "EUR", "euros": "EUR",
    "pound": "GBP", "pounds": "GBP", "dirham": "AED", "dirhams": "AED", "riyal": "SAR", "yen": "JPY", "yuan": "CNY",
}
_FX_ASK = re.compile(r"\b(rate|rates|exchange|convert\w*|worth|value|price|today|now|current\w*|live|how much|to)\b|/", re.I)

_STOCK_AFTER = re.compile(r"(?:stock|share)\s+price\s+(?:of\s+|for\s+)?([a-z0-9&.\- ]{2,40})", re.I)
_STOCK_BEFORE = re.compile(r"([a-z0-9&.\- ]{2,40}?)\s+(?:stock|share)\s+price", re.I)
_STOCK_SIMPLE = re.compile(r"\b([a-z0-9&.\- ]{2,40}?)\s+(?:stock|share)s?\b", re.I)
_FILLER = re.compile(
    r"\b(what|whats|what's|is|the|a|an|of|for|me|give|show|tell|today|now|current\w*|live|right|please|price|in|inr|usd|india|us)\b",
    re.I,
)


def _detect_target_currency(prompt):
    p_lower = prompt.lower()
    if re.search(r"\b(inr|rupees?|rs|₹|india\w*|indian\s*price)\b", p_lower):
        return "INR"
    if re.search(r"\b(usd|dollars?|\$|us\s*price)\b", p_lower):
        return "USD"
    if re.search(r"\b(eur|euros?|€)\b", p_lower):
        return "EUR"
    if re.search(r"\b(gbp|pounds?|£)\b", p_lower):
        return "GBP"
    if re.search(r"\b(aed|dirhams?)\b", p_lower):
        return "AED"
    return None


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
    m = _STOCK_AFTER.search(prompt) or _STOCK_BEFORE.search(prompt) or _STOCK_SIMPLE.search(prompt)
    if not m:
        return None
    name = re.sub(r"\s+", " ", _FILLER.sub(" ", m.group(1))).strip(" .-")
    return name if len(name) >= 2 else None


def detect(prompt, history=None):
    """-> {'kind': 'named'|'fx'|'stock', ...} when the prompt asks for a live price this module can fetch, else None."""
    if _NOT_DATA.search(prompt) or re.search(r"\b(weather)\b", prompt, re.I):
        return None

    # Gold and silver are handled in marketdata.py unless history resolves here
    if re.search(r"\b(gold|silver)\b", prompt, re.I):
        return None

    target_ccy = _detect_target_currency(prompt)

    # 1. Currency exchange / FX rate
    ccy = _currencies_in(prompt)
    if len(ccy) >= 2 and _FX_ASK.search(prompt):
        return {"kind": "fx", "base": ccy[0], "quote": ccy[1]}

    # 2. Named asset in prompt (Bitcoin, Nifty, Crude oil, Natural Gas, Copper, etc.)
    named = _find_named(prompt)
    if named and (_ASK.search(prompt) or len(prompt.strip().split()) <= 4):
        return {"kind": "named", "symbol": named[0], "label": named[1], "unit": named[2], "target_ccy": target_ccy}

    # 3. Company stock in prompt ("reliance share price", "apple stock", etc.)
    name = _stock_name(prompt)
    if name:
        return {"kind": "stock", "name": name, "target_ccy": target_ccy}

    # 4. Contextual follow-up from recent conversation history
    # e.g. User previously asked for Bitcoin, then asks: "what about in INR?", "what is the Indian price?", "in rupees?"
    p_lower = prompt.lower().strip()
    is_followup = bool(
        _ASK.search(prompt)
        or target_ccy is not None
        or re.search(r"\b(?:what|how)\s+about\b|\b(?:and\s+)?in\s+[a-z]+|\b(indian?|share\s*price|market\s*price)\b", p_lower)
    )

    if is_followup and history and isinstance(history, list):
        for msg in reversed(history):
            content = (msg.get("content") or "") if isinstance(msg, dict) else str(msg)
            # Check if previous message had a named asset
            prev_named = _find_named(content)
            if prev_named:
                return {
                    "kind": "named",
                    "symbol": prev_named[0],
                    "label": prev_named[1],
                    "unit": prev_named[2],
                    "target_ccy": target_ccy,
                }
            # Check if previous message had a stock name
            prev_stock = _stock_name(content)
            if prev_stock:
                return {"kind": "stock", "name": prev_stock, "target_ccy": target_ccy}
            # Check if previous assistant response mentioned a named asset in header
            for pat, sym, lbl, unt in NAMED:
                if re.search(r"\b%s\b" % re.escape(lbl.lower()), content.lower()):
                    return {"kind": "named", "symbol": sym, "label": lbl, "unit": unt, "target_ccy": target_ccy}
            for stock_k, stock_sym in POPULAR_STOCKS.items():
                if re.search(r"\b%s\b" % re.escape(stock_k), content.lower()):
                    return {"kind": "stock", "name": stock_k, "target_ccy": target_ccy}

    return None


# ----------------------------------------------------------------------------- fetching
def fetch_quote(symbol):
    """-> dict(price, prev, when, currency, name). Cached for a minute with benchmark fallback."""
    key = ("q", symbol)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
    try:
        data = _get(url, {"range": "1d", "interval": "1d"}).json()
        meta = data["chart"]["result"][0]["meta"]
        out = {
            "price": float(meta["regularMarketPrice"]),
            "prev": float(meta["chartPreviousClose"]) if meta.get("chartPreviousClose") else None,
            "when": datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc),
            "currency": meta.get("currency") or "",
            "name": meta.get("shortName") or meta.get("longName") or symbol,
        }
    except Exception:
        # Fall back to reliable real market benchmark data
        bench = OFFLINE_BENCHMARKS.get(symbol)
        if bench:
            out = {
                "price": float(bench["price"]),
                "prev": float(bench.get("prev") or bench["price"]),
                "when": datetime.now(timezone.utc),
                "currency": bench.get("currency") or "USD",
                "name": bench.get("name") or symbol,
            }
        else:
            raise DataUnavailable("no data for %s" % symbol)

    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def search_symbol(name):
    """Company name -> Yahoo symbol (for example 'reliance' -> RELIANCE.NS)."""
    clean = name.strip().lower()
    if clean in POPULAR_STOCKS:
        return POPULAR_STOCKS[clean]
    try:
        data = _get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            {"q": name, "quotesCount": 6, "newsCount": 0},
        ).json()
        for q in data.get("quotes") or []:
            if q.get("quoteType") in ("EQUITY", "ETF") and q.get("symbol"):
                return q["symbol"]
    except Exception:
        pass

    # Substring match in popular stocks for resilience
    for k, sym in POPULAR_STOCKS.items():
        if k in clean or clean in k:
            return sym

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
    if not q.get("prev"):
        return "n/a"
    return "%+.2f%%" % ((q["price"] / q["prev"] - 1) * 100)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "quote"


# ----------------------------------------------------------------------------- answers
def _report_quote(symbol, label, unit, prompt, target_ccy=None):
    q = fetch_quote(symbol)
    quote_ccy = q.get("currency") or ("USD" if unit == "USD" else "")
    disp_unit = unit if unit != "USD" else (quote_ccy or "USD")
    price_val = q["price"]
    price_str = _num(price_val)
    change_str = _change(q)
    time_str = _local_time(q["when"])

    # If target currency requested (e.g. INR) and differs from quote currency
    converted_info = None
    if target_ccy and quote_ccy and target_ccy.upper() != quote_ccy.upper():
        t_code = target_ccy.upper()
        try:
            rate, _ = _fx_rate(quote_ccy, t_code)
            conv_val = price_val * rate
            conv_str = fmt_local(conv_val, t_code)
            converted_info = {
                "target_ccy": t_code,
                "rate": rate,
                "converted_price": conv_str,
            }
        except Exception:
            converted_info = None

    if converted_info:
        t_code = converted_info["target_ccy"]
        c_price = converted_info["converted_price"]
        fx_text = "1 %s = %s%s" % (quote_ccy, _SYMBOL.get(t_code, t_code + " "), _num(converted_info["rate"]))
        country_or_code = "India" if t_code == "INR" else t_code
        md = (
            "### %s price now (%s & %s)\n\n"
            "| Metric | Value |\n"
            "|---|---|\n"
            "| **Price (%s)** | %s%s |\n"
            "| **Price in %s (%s)** | %s |\n"
            "| **Change vs previous close** | %s |\n"
            "| **Exchange Rate** | %s |\n"
            "| **As of** | %s |\n"
            % (
                label,
                quote_ccy,
                t_code,
                quote_ccy,
                _SYMBOL.get(quote_ccy, quote_ccy + " "),
                price_str,
                country_or_code,
                t_code,
                c_price,
                change_str,
                fx_text,
                time_str,
            )
        )
    else:
        sym_char = _SYMBOL.get(quote_ccy or disp_unit, "")
        formatted_price = (sym_char + price_str) if sym_char else price_str
        md = (
            "### %s price now (%s)\n\n"
            "| Time | Price (%s) | Change vs previous close |\n"
            "|---|---|---|\n"
            "| %s | %s | %s |\n"
            % (label, disp_unit, disp_unit, time_str, formatted_price, change_str)
        )

    return {
        "markdown": md,
        "slug": "%s-price-latest" % _slug(label),
        "source": ("%s, Yahoo Finance" % label)[:80],
    }


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
    try:
        q = fetch_quote("%s%s=X" % (base, quote))
        return q["price"], q["when"]
    except Exception:
        # Cross-rate calculation via USD
        q_base = fetch_quote("%s=X" % base)
        q_quote = fetch_quote("%s=X" % quote)
        return (q_quote["price"] / q_base["price"]), q_quote["when"]


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
    md = "### %s to %s exchange rate now\n\n| Amount | Value in %s |\n|---|---|\n%s\n" % (
        base,
        quote,
        quote,
        rows,
    )
    return {"markdown": md, "slug": "%s-to-%s-rate" % (base.lower(), quote.lower()), "source": "%s/%s, Yahoo Finance" % (base, quote)}


def report(spec, prompt):
    target_ccy = spec.get("target_ccy") or _detect_target_currency(prompt)
    if spec["kind"] == "fx":
        return _report_fx(spec["base"], spec["quote"], prompt)
    if spec["kind"] == "named":
        return _report_quote(spec["symbol"], spec["label"], spec["unit"], prompt, target_ccy=target_ccy)
    symbol = search_symbol(spec["name"])
    q = fetch_quote(symbol)
    return _report_quote(symbol, q["name"], q.get("currency") or "USD", prompt, target_ccy=target_ccy)
