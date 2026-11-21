"""REAL weather data from Open-Meteo (free, no API key): past days (observed) and up to 16 days ahead (forecast).

The AI model never writes these numbers. Dates that no source can know (e.g. December 2026 when today is
November 2026) are refused with a clear message instead of being invented.
"""
import re
import threading
import time
from datetime import date, datetime, timedelta

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

_WEATHER = re.compile(r"\b(weather|temperature|temp|forecast|rainfall|rain)\b", re.I)
_NOT_DATA = re.compile(r"\b(why|explain|reason|cause|causes|climate change|affect\w*)\b", re.I)
_STOP = set("""weather temperature temp temps forecast report data climate rain rainfall for in of at the a an me my give show
tell what whats what's is are how today tomorrow tonight now current curret last past next previous days day week weeks
hourly daily please city india table chart list format with and also details detail information info want need get see
from to on will be going like it excel pdf word csv docx json html txt md ppt pptx powerpoint spreadsheet file
january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept
oct nov dec year years month months can you do tell about""".split())


def detect(prompt):
    return bool(_WEATHER.search(prompt)) and not _NOT_DATA.search(prompt)


def extract_city(prompt):
    p = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\d+", " ", prompt.lower())
    words = [w.strip(".'-") for w in re.findall(r"[a-z][a-z'.-]*", p)]
    keep = [w for w in words if w and w not in _STOP]
    return " ".join(keep).title() or None


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
    if re.search(r"\b(?:last|past)\s+week\b", p):
        return today - timedelta(days=6), today
    if re.search(r"\bnext\s+week\b", p):
        return today, today + timedelta(days=6)
    if re.search(r"\btomorrow\b", p):
        return today + timedelta(days=1), today + timedelta(days=1)
    if re.search(r"\b(?:today|now|current\w*)\b", p):
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
        resp = requests.get(url, params=params, timeout=(5, 15), headers={"User-Agent": "UpianAI/1.0"})
    except requests.RequestException as exc:
        raise DataUnavailable("network error: %s" % str(exc)[:100])
    if resp.status_code >= 400:
        raise DataUnavailable("HTTP %s from the weather service" % resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise DataUnavailable("weather service returned invalid data")


def geocode(city):
    key = ("geo", city.lower())
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
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
    d = _get_json(url, params).get("daily") or {}
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
        rows.append((datetime.strptime(day, "%Y-%m-%d").date(), tmax, tmin, rain, CODES.get(code, "n/a")))
    if not rows:
        raise DataUnavailable("the weather service has no values for these dates yet")
    with _LOCK:
        _CACHE[key] = (time.time(), rows)
    return rows


def report(prompt, today=None):
    today = today or date.today()
    city = extract_city(prompt)
    if not city:
        raise NeedInfo("Which city? For example: `weather in Surat for the last 10 days`.")
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
    lines = ["### Weather in %s: %s to %s" % (place, rows[0][0].isoformat(), rows[-1][0].isoformat()), "",
             "| Date | Max (°C) | Min (°C) | Rain (mm) | Condition |", "|---|---|---|---|---|"]
    for d, tmax, tmin, rain, cond in rows:
        lines.append("| %s | %s | %s | %s | %s |" % (d.strftime("%a, %d %b %Y"), fmt(tmax), fmt(tmin), fmt(rain), cond))
    slug = "weather-%s-%s-to-%s" % (re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-"), rows[0][0].isoformat(),
                                    rows[-1][0].isoformat())
    return {"markdown": "\n".join(lines), "slug": slug, "source": "Open-Meteo weather"}
