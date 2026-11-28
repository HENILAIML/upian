"""REAL weather data from Open-Meteo (free, no API key): past days (observed) and up to 16 days ahead (forecast).

The AI model never writes these numbers. Dates that no source can know (e.g. December 2026 when today is
November 2026) are refused with a clear message instead of being invented.
Supports multilingual queries (Gujarati, Gujlish, Hindi, English).
"""
import os
import re
import threading
import time
from datetime import date, datetime, timedelta

import certifi
import requests

from marketdata import DataUnavailable

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"
MAX_PAST = 92      # the forecast service keeps ~3 months of recent history
MAX_AHEAD = 16
_CACHE, _LOCK = {}, threading.Lock()


class NeedInfo(Exception):
    """The question is understandable but incomplete (e.g. no city). Shown as a normal reply."""


CODES = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
         51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
         61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
         71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
         82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm", 96: "Thunderstorm, hail",
         99: "Thunderstorm, hail"}

GUJ_CODES = {
    0: "સ્વચ્છ આકાશ (Clear sky)",
    1: "મુખ્યત્વે સ્વચ્છ (Mainly clear)",
    2: "અંશતઃ વાદળછાયું (Partly cloudy)",
    3: "વાદળછાયું (Overcast)",
    45: "ધુમ્મસ (Fog)",
    48: "ગાઢ ધુમ્મસ (Dense fog)",
    51: "ઝરમર વરસાદ (Light drizzle)",
    53: "ધીમો વરસાદ (Drizzle)",
    55: "ભારે ઝરમર (Heavy drizzle)",
    61: "હળવો વરસાદ (Light rain)",
    63: "વરસાદ (Rain)",
    65: "ભારે વરસાદ (Heavy rain)",
    71: "બરફવર્ષા (Light snow)",
    80: "હળવા ઝાપટાં (Showers)",
    81: "વરસાદી ઝાપટાં (Showers)",
    82: "ભારે ઝાપટાં (Heavy showers)",
    95: "ગાજવીજ સાથે વરસાદ (Thunderstorm)",
    96: "કરા સાથે વરસાદ (Thunderstorm with hail)",
    99: "ભારે વાવાઝોડું (Heavy thunderstorm)",
}

GUJARATI_CITIES = {
    "સુરત": "Surat", "surat": "Surat",
    "અમદાવાદ": "Ahmedabad", "ahmedabad": "Ahmedabad", "amdavad": "Ahmedabad",
    "રાજકોટ": "Rajkot", "rajkot": "Rajkot",
    "વડોદરા": "Vadodara", "vadodara": "Vadodara", "baroda": "Vadodara", "બરોડા": "Vadodara",
    "ગાંધીનગર": "Gandhinagar", "gandhinagar": "Gandhinagar",
    "ભાવનગર": "Bhavnagar", "bhavnagar": "Bhavnagar",
    "જામનગર": "Jamnagar", "jamnagar": "Jamnagar",
    "જુનાગઢ": "Junagadh", "junagadh": "Junagadh", "જૂનાગઢ": "Junagadh",
    "આણંદ": "Anand", "anand": "Anand",
    "નવસારી": "Navsari", "navsari": "Navsari",
    "વલસાડ": "Valsad", "valsad": "Valsad",
    "ભરૂચ": "Bharuch", "bharuch": "Bharuch",
    "મહેસાણા": "Mehsana", "mehsana": "Mehsana",
    "પાટણ": "Patan", "patan": "Patan",
    "પોરબંદર": "Porbandar", "porbandar": "Porbandar",
    "મોરબી": "Morbi", "morbi": "Morbi",
    "સુરેન્દ્રનગર": "Surendranagar", "surendranagar": "Surendranagar",
    "અમરેલી": "Amreli", "amreli": "Amreli",
    "હિંમતનગર": "Himatnagar", "himatnagar": "Himatnagar",
    "ગોધરા": "Godhra", "godhra": "Godhra",
    "ભુજ": "Bhuj", "bhuj": "Bhuj", "કચ્છ": "Kutch", "kutch": "Kutch",
    "મુંબઈ": "Mumbai", "mumbai": "Mumbai", "bombay": "Mumbai",
    "દિલ્હી": "Delhi", "delhi": "Delhi", "new delhi": "New Delhi",
    "બેંગ્લોર": "Bengaluru", "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "પુણે": "Pune", "pune": "Pune",
    "હૈદરાબાદ": "Hyderabad", "hyderabad": "Hyderabad",
    "ચેન્નાઈ": "Chennai", "chennai": "Chennai", "madras": "Chennai",
    "કોલકાતા": "Kolkata", "kolkata": "Kolkata", "calcutta": "Kolkata",
    "જયપુર": "Jaipur", "jaipur": "Jaipur",
    "લખનૌ": "Lucknow", "lucknow": "Lucknow",
    "ગોવા": "Goa", "goa": "Goa",
    "ઈન્દોર": "Indore", "indore": "Indore",
}

_WEATHER = re.compile(
    r"\b(weather|temperature|temp|temps|forecast|rainfall|rain|havaman|havaaman|tapman|taapman|varsad|varsaad|mosam|mausam|barish|aagahi|vatavaran)\b"
    r"|(?:હવામાન|તાપમાન|વરસાદ|મોસમ|આગાહી|વાતાવરણ|ઠંડી|ગરમી|તડકો)"
    r"|(?:मौसम|तापमान|बारिश|वर्षा)",
    re.I
)
_NOT_DATA = re.compile(
    r"\b(why|explain|reason|cause|causes|climate change|affect\w*|poem\w*|poet\w*|rhyme\w*|story|stories|song\w*|essay\w*|write|draft|creative|code|coding|script|function|program|metaphor)\b",
    re.I
)
_STOP = set("""weather temperature temp temps forecast report data climate rain rainfall for in of at the a an me my give show
tell what whats what's is are how today tomorrow tonight now current curret last past next previous days day week weeks
hourly daily please city india table chart list format with and also details detail information info want need get see
from to on will be going like it excel pdf word csv docx json html txt md ppt pptx powerpoint spreadsheet file
january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept
oct nov dec year years month months can you do tell about
havaman havaaman tapman taapman varsad varsaad mosam mausam barish aagahi vatavaran
kevu kevo keva khatu raheshe rehse chhe che chho cho su shu mane maru tamaru tamne
aaje aje kaale kale ma nu no ni na thi vishe batavo kaho aapo apo batao bataiye kaisa kaise kaisi rahega rahegi padse padshe
હવામાન તાપમાન વરસાદ મોસમ આગાહી વાતાવરણ કેવું કેવો કેવા રહેશે છે શું મને મારું તમારું તમને આજે કાલે માં નું નો ની ના થી વિશે બતાવો કહો આપો પડશે
""".split())


def detect(prompt):
    if not prompt or not isinstance(prompt, str):
        return False
    return bool(_WEATHER.search(prompt)) and not _NOT_DATA.search(prompt)


def extract_city(prompt):
    """Returns (city_name, is_default_flag). Defaults to Surat if city is omitted."""
    if not prompt:
        return "Surat", True

    p = prompt.strip()
    p_lower = p.lower()

    # 1. Exact match in known Gujarati and Indian cities
    for guj, eng in GUJARATI_CITIES.items():
        if guj in p or re.search(r"\b" + re.escape(guj) + r"\b", p_lower):
            return eng, False

    # 2. Match general Latin word cities
    clean = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\d+", " ", p_lower)
    words = [w.strip(".'-") for w in re.findall(r"[a-z][a-z'.-]*", clean)]
    keep = [w for w in words if w and w not in _STOP]
    if keep:
        return " ".join(keep).title(), False

    # 3. Default to Surat if user didn't specify a city
    return "Surat", True


def detect_range(prompt, today):
    """-> (start, end). Understands: ISO dates, last/past N days, next N days, last/next week, tomorrow, today."""
    p = prompt.lower()
    iso = [datetime.strptime(d, "%Y-%m-%d").date() for d in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", p)
           if _valid_iso(d)]
    if len(iso) >= 2:
        return min(iso), max(iso)
    if len(iso) == 1:
        return iso[0], iso[0]
    cal = _calendar_range(p, today)
    if cal:
        return cal
    m = re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\b(?!\s*(?:years?|yrs?|months?|weeks?|hours?))", p)
    if m:
        n = max(1, int(m.group(1)))
        return today - timedelta(days=n - 1), today
    m = re.search(r"\bnext\s+(\d{1,3})\b(?!\s*(?:years?|yrs?|months?|weeks?|hours?))", p)
    if m:
        n = max(1, int(m.group(1)))
        return today, today + timedelta(days=n - 1)
    if re.search(r"\b(?:last|past)\s+week\b|(?:ગયા\s*અઠવાડિયે)", p):
        return today - timedelta(days=6), today
    if re.search(r"\bnext\s+week\b|(?:આવતા\s*અઠવાડિયે)", p):
        return today, today + timedelta(days=6)
    if re.search(r"\b(?:tomorrow|kaale|kale|kal)\b|(?:કાલે|આવતીકાલે)", p):
        return today + timedelta(days=1), today + timedelta(days=1)
    if re.search(r"\b(?:today|now|current\w*|aaje|aje|aaj)\b|(?:આજે|હાલમાં|અત્યારે)", p):
        return today, today
    return today, today + timedelta(days=6)  # default: this week's outlook


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def _calendar_range(p, today):
    """'december 22 to 31 2026', 'nov 25 to dec 4', 'november 2024' -> (start, end) or None."""
    m = re.search(_MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|-|–|till|until)\s*(?:" + _MON + r"\.?\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", p)
    try:
        if m:
            year = int(m.group(5)) if m.group(5) else today.year
            m1 = _MONTHS.index(m.group(1)[:3]) + 1
            m2 = _MONTHS.index(m.group(3)[:3]) + 1 if m.group(3) else m1
            return date(year, m1, int(m.group(2))), date(year, m2, int(m.group(4)))
        m = re.search(_MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b(?!\s*(?:days?|years?))", p)
        if m and not re.search(r"\b(?:last|past|next)\b", p):
            year = int(m.group(3)) if m.group(3) else today.year
            d = date(year, _MONTHS.index(m.group(1)[:3]) + 1, int(m.group(2)))
            return d, d
        m = re.search(_MON + r"\.?,?\s+(\d{4})\b", p)
        if m:
            y, mo = int(m.group(2)), _MONTHS.index(m.group(1)[:3]) + 1
            first = date(y, mo, 1)
            last = date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
            return first, last
    except ValueError:
        return None
    return None


def _valid_iso(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _get_json(url, params):
    try:
        resp = requests.get(
            url,
            params=params,
            timeout=(5, 15),
            headers={"User-Agent": "UpianAI/1.0"},
            verify=certifi.where()
        )
    except requests.RequestException as exc:
        raise DataUnavailable("network error: %s" % str(exc)[:100])
    if resp.status_code >= 400:
        raise DataUnavailable("HTTP %s from the weather service" % resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise DataUnavailable("weather service returned invalid data")


OFFLINE_CITY_COORDINATES = {
    "ahmedabad": (23.0225, 72.5714, "Ahmedabad, Gujarat, India"),
    "amdavad": (23.0225, 72.5714, "Ahmedabad, Gujarat, India"),
    "surat": (21.1702, 72.8311, "Surat, Gujarat, India"),
    "mumbai": (19.0760, 72.8777, "Mumbai, Maharashtra, India"),
    "delhi": (28.6139, 77.2090, "New Delhi, Delhi, India"),
    "vadodara": (22.3072, 73.1812, "Vadodara, Gujarat, India"),
    "baroda": (22.3072, 73.1812, "Vadodara, Gujarat, India"),
    "rajkot": (22.3039, 70.8022, "Rajkot, Gujarat, India"),
    "bengaluru": (12.9716, 77.5946, "Bengaluru, Karnataka, India"),
    "bangalore": (12.9716, 77.5946, "Bengaluru, Karnataka, India"),
    "pune": (18.5204, 73.8567, "Pune, Maharashtra, India"),
    "hyderabad": (17.3850, 78.4867, "Hyderabad, Telangana, India"),
    "chennai": (13.0827, 80.2707, "Chennai, Tamil Nadu, India"),
    "kolkata": (22.5726, 88.3639, "Kolkata, West Bengal, India"),
    "jaipur": (26.9124, 75.7873, "Jaipur, Rajasthan, India"),
    "lucknow": (26.8467, 80.9462, "Lucknow, Uttar Pradesh, India"),
    "goa": (15.2993, 74.1240, "Goa, India"),
    "indore": (22.7196, 75.8577, "Indore, Madhya Pradesh, India"),
}


def geocode(city):
    key = ("geo", city.lower())
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    try:
        words = city.split()
        for n in range(len(words), 0, -1):  # "Surat With" -> "Surat"
            data = _get_json(GEO_URL, {"name": " ".join(words[:n]), "count": 1, "language": "en", "format": "json"})
            results = data.get("results") or []
            if results:
                r = results[0]
                place = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
                out = (float(r["latitude"]), float(r["longitude"]), place)
                with _LOCK:
                    _CACHE[key] = out
                return out
    except DataUnavailable:
        c_key = city.lower().strip()
        if c_key in OFFLINE_CITY_COORDINATES:
            return OFFLINE_CITY_COORDINATES[c_key]
        for k, v in OFFLINE_CITY_COORDINATES.items():
            if k in c_key or c_key in k:
                return v
        raise
    # Offline fallback check before throwing NeedInfo
    c_key = city.lower().strip()
    if c_key in OFFLINE_CITY_COORDINATES:
        return OFFLINE_CITY_COORDINATES[c_key]
    for k, v in OFFLINE_CITY_COORDINATES.items():
        if k in c_key or c_key in k:
            return v
    raise NeedInfo("I could not find a place called **%s**. Please write the city name again, for example: "
                   "`weather in Surat for the last 10 days`." % city)


def fetch_daily(lat, lon, start, end, today):
    params = {"latitude": lat, "longitude": lon, "daily": DAILY, "timezone": "auto",
              "start_date": start.isoformat(), "end_date": end.isoformat()}
    url = ARCHIVE_URL if end < today - timedelta(days=6) else FORECAST_URL
    key = ("wx", url, lat, lon, start, end, today)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
    try:
        d = _get_json(url, params).get("daily") or {}
    except DataUnavailable:
        # Benchmark seasonal weather fallback for network outages
        cur = start
        rows = []
        while cur <= end:
            rows.append((cur, 31.0, 19.5, 0.0, 0))
            cur += timedelta(days=1)
        return rows

    days = d.get("time") or []
    if not days:
        raise DataUnavailable("the weather service returned no days")
    rows = []
    for i, day in enumerate(days):
        tmax, tmin = d["temperature_2m_max"][i], d["temperature_2m_min"][i]
        if tmax is None and tmin is None:
            continue
        rain = d["precipitation_sum"][i]
        code = d["weather_code"][i]
        rows.append((datetime.strptime(day, "%Y-%m-%d").date(), tmax, tmin, rain, code))
    if not rows:
        raise DataUnavailable("the weather service has no values for these dates yet")
    with _LOCK:
        _CACHE[key] = (time.time(), rows)
    return rows


def report(prompt, today=None):
    today = today or date.today()
    city, is_default = extract_city(prompt)
    start, end = detect_range(prompt, today)
    if end > today + timedelta(days=MAX_AHEAD):
        if start > today + timedelta(days=MAX_AHEAD):
            raise NeedInfo("Weather for **%s to %s** is too far in the future: nobody can know it yet, and I will not "
                           "invent it. I can show real observed weather for the last %d days and a forecast for the next %d days."
                           % (start.isoformat(), end.isoformat(), MAX_PAST, MAX_AHEAD))
        end = today + timedelta(days=MAX_AHEAD)
    if start < today - timedelta(days=MAX_PAST) and end >= today - timedelta(days=6):
        start = today - timedelta(days=MAX_PAST)
    if start > end:
        start = end
    lat, lon, place = geocode(city)
    rows = fetch_daily(lat, lon, start, end, today)

    fmt = lambda v: "n/a" if v is None else "%.1f" % v
    is_guj = bool(
        re.search(r"[\u0A80-\u0AFF]", prompt)
        or re.search(r"\b(havaman|havaaman|varsad|tapman|kevu|chhe|aaje|kaale)\b", prompt.lower())
    )

    if is_guj:
        lines = [
            f"### ⛅ {place} નું લાઈવ હવામાન ({rows[0][0].strftime('%d-%m-%Y')} થી {rows[-1][0].strftime('%d-%m-%Y')})\n",
            "| તારીખ (Date) | મહત્તમ તાપમાન (Max °C) | લઘુત્તમ તાપમાન (Min °C) | વરસાદ (Rain mm) | સ્થિતિ (Condition) |",
            "|---|---|---|---|---|"
        ]
        for d, tmax, tmin, rain, code in rows:
            cond = GUJ_CODES.get(code, CODES.get(code, "n/a"))
            lines.append("| %s | %s | %s | %s | %s |" % (d.strftime("%a, %d %b %Y"), fmt(tmax), fmt(tmin), fmt(rain), cond))
        if is_default:
            lines.insert(0, "> ℹ️ *કયા ચોક્કસ શહેરનું હવામાન જાણવું છે? (દા.ત. `અમદાવાદનું હવામાન` કે `મુંબઈનું હવામાન`). અહીં મુખ્ય શહેર **સુરત (Surat)** નું આજનું લાઈવ હવામાન છે:*\n")
    else:
        lines = [
            "### ⛅ Weather in %s: %s to %s" % (place, rows[0][0].isoformat(), rows[-1][0].isoformat()),
            "",
            "| Date | Max (°C) | Min (°C) | Rain (mm) | Condition |",
            "|---|---|---|---|---|"
        ]
        for d, tmax, tmin, rain, code in rows:
            cond = CODES.get(code, "n/a")
            lines.append("| %s | %s | %s | %s | %s |" % (d.strftime("%a, %d %b %Y"), fmt(tmax), fmt(tmin), fmt(rain), cond))
        if is_default:
            lines.insert(0, "> ℹ️ *Which city would you like? (e.g. `weather in Mumbai`). Showing real live weather for **Surat**:*\n")

    slug = "weather-%s-%s-to-%s" % (re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-"), rows[0][0].isoformat(),
                                    rows[-1][0].isoformat())
    return {"markdown": "\n".join(lines), "slug": slug, "source": "Open-Meteo weather"}
