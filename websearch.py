"""Real-time live internet web search for Upian AI (no paid API key needed).
Uses DuckDuckGo Instant Answers & Wikipedia API to fetch live summaries and citations.
"""
import html
import re
import urllib.parse
import certifi
import requests

from marketdata import DataUnavailable

_SEARCH_TRIGGERS = re.compile(
    r"\b(search\s+(the\s+)?(web|internet|google|bing)|web\s*search|google\s+(it|for)|latest\s+news|today'?s\s+news|who\s+won|current\s+news)\b"
    r"|(?:ઈન્ટરનેટ\s*પર\s*શોધો|સર્ચ\s*કરો|લાઈવ\s*સમાચાર|વેબ\s*સર્ચ)",
    re.I
)

_CLEAN_PREFIX = re.compile(
    r"^(?:search\s+(?:the\s+)?(?:web|internet|google)\s+(?:for\s+)?|web\s*search\s+(?:for\s+)?|google\s+(?:it\s+for\s+|for\s+)?|ઈન્ટરનેટ\s*પર\s*શોધો\s*|સર્ચ\s*કરો\s*)",
    re.I
)


def detect(prompt: str) -> bool:
    if not prompt or not isinstance(prompt, str):
        return False
    return bool(_SEARCH_TRIGGERS.search(prompt))


def extract_query(prompt: str) -> str:
    p = prompt.strip()
    q = _CLEAN_PREFIX.sub("", p).strip()
    return q or p


def search(prompt: str) -> dict:
    q = extract_query(prompt)
    if not q:
        raise DataUnavailable("Please specify what you would like to search for.")

    headers = {"User-Agent": "UpianAI/1.0 (https://upian.ai; contact@upian.ai)"}
    verify = certifi.where()

    # 1. Try DuckDuckGo Instant Answer API
    try:
        ddg_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(q)}&format=json&no_html=1&skip_disambig=1"
        resp = requests.get(ddg_url, headers=headers, verify=verify, timeout=(4, 10))
        if resp.status_code == 200:
            data = resp.json()
            abstract = (data.get("Abstract") or "").strip()
            heading = (data.get("Heading") or "").strip()
            source_url = (data.get("AbstractURL") or "").strip()
            if abstract and heading:
                lines = [
                    f"### 🌐 Real-Time Web Search: **{heading}**\n",
                    abstract,
                    f"\n\n**Source:** [{source_url or 'DuckDuckGo'}]({source_url or 'https://duckduckgo.com/?q=' + urllib.parse.quote(q)})"
                ]
                slug = "web-search-" + re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:30]
                return {"markdown": "\n".join(lines), "slug": slug, "source": "Live Web Search"}
    except Exception:
        pass

    # 2. Wikipedia Search API
    try:
        is_guj = bool(re.search(r"[\u0A80-\u0AFF]", q))
        wiki_lang = "gu" if is_guj else "en"
        wiki_url = f"https://{wiki_lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(q)}&utf8=&format=json"
        resp = requests.get(wiki_url, headers=headers, verify=verify, timeout=(4, 10))
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("query", {}).get("search", [])[:4]
            if items:
                lines = [f"### 🌐 Real-Time Web Search Results for: *\"{q}\"*\n"]
                for it in items:
                    title = it.get("title", "")
                    raw_snip = html.unescape(re.sub(r"<[^<]+?>", "", it.get("snippet", "")))
                    link = f"https://{wiki_lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                    lines.append(f"- **[{title}]({link})**\n  {raw_snip}...\n")
                lines.append(f"\n*Live search retrieved from web sources for query: \"{q}\"*")
                slug = "web-search-" + re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:30]
                return {"markdown": "\n".join(lines), "slug": slug, "source": "Live Web Search"}
    except Exception as exc:
        raise DataUnavailable(f"Web search could not retrieve live data: {str(exc)[:100]}")

    raise DataUnavailable(f"No real-time web results found for '{q}'. Please try different keywords.")
