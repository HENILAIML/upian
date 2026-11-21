"""Chat shortcuts that must NOT be answered by a language model:
  1. "gold price 2020 to 2030", "gold price in india", "bitcoin price", "usd to inr", "weather in Surat"
     -> real data, returned as a table (never invented by the AI)
  2. "give me that in excel/pdf/word/csv/ppt/json/html/txt/md" -> the previous answer turned into a real file
Both can be combined: "gold price last 10 years in excel".
"""
import re

from datetime import datetime, timezone

import filetools
import marketdata
import quotes
import weather
from config import Config
from file_routes import save_export

TIP = ""


class SpecialError(Exception):
    """Shown to the user as-is; the request is not counted."""


# format -> (unambiguous words, words that only count after "in / as / to ...")
FORMATS = {
    "pdf": ("pdf", ""),
    "xlsx": ("excel|xlsx|xls|spreadsheet", "sheet"),
    "csv": ("csv", ""),
    "pptx": ("pptx|powerpoint|power point", "ppt|slides?|presentation"),
    "docx": ("docx", "word|doc"),
    "json": ("json", ""),
    "html": ("html", ""),
    "md": ("markdown", "md"),
    "txt": ("txt", "text file|plain text|text"),
}
_PREP = (r"\b(?:in|as|into|to|export|convert|download|save)\s+(?:me\s+|it\s+|this\s+|that\s+)?"
         r"(?:an?\s+|the\s+)?(?:ms\s+|microsoft\s+)?\.?")
_QUESTION = re.compile(r"^\s*(what|why|how|who|when|where|explain|tell me about|define)\b", re.I)
_CODING = re.compile(r"\b(script|code|function|program|python|javascript|java|library|module|package|api)\b", re.I)


def detect_formats(prompt, anywhere=False):
    """All file formats the user asked for ("in pdf and excel" -> ['pdf', 'xlsx']). [] when it is not an export request."""
    p = prompt.lower().strip()
    if not anywhere and (len(p.split()) > 14 or _QUESTION.match(p) or _CODING.search(p)):
        return []
    found, confident = [], False
    for fmt, (strong, weak) in FORMATS.items():
        alts = "|".join(x for x in (strong, weak) if x)
        if re.search(_PREP + r"(?:" + alts + r")\b", p) or re.search(r"\b(?:" + alts + r")\s+(?:file|format|document|version)\b", p):
            found.append(fmt)
            confident = True
    if not confident and (anywhere or len(p.split()) <= 8):
        for fmt, (strong, weak) in FORMATS.items():
            if strong and re.search(r"\b(?:" + strong + r")\b", p):
                found.append(fmt)
    if found:  # "in pdf AND excel" / "pdf, word and ppt"
        for fmt, (strong, weak) in FORMATS.items():
            alts = "|".join(x for x in (strong, weak) if x)
            if fmt not in found and re.search(r"(?:\band\b|,|&|\+|\bor\b)\s*(?:an?\s+|the\s+)?(?:ms\s+)?(?:" + alts + r")\b", p):
                found.append(fmt)
    return found


def detect_format(prompt, anywhere=False):
    found = detect_formats(prompt, anywhere)
    return found[0] if found else None



_TIME = re.compile(r"\b(time|date)\b", re.I)
_NOWISH = re.compile(r"\b(cur\w*|now|today\w*|present|right now)\b", re.I)


def is_time_question(prompt):
    p = prompt.strip().lower()
    if len(p.split()) > 8 or re.search(r"\b(gold|silver|weather|python|code|zone|convert)\b", p):
        return False
    return bool(_TIME.search(p) and (_NOWISH.search(p) or re.match(r"^(what|whats|what's|tell me|show me)\b.*\b(time|date)\b", p))) \
        or bool(re.search(r"\bwhat day is (it|today)\b", p))


def now_reply():
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(Config.APP_TIMEZONE)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    return "It is **%s**, %s (%s, %s)." % (now.strftime("%A, %d %B %Y"), now.strftime("%I:%M %p").lstrip("0"),
                                           Config.APP_TIMEZONE, now.strftime("%Z") or "UTC")


def last_exportable(history):
    """Text of the newest assistant answer that is not itself a 'here is your file' message."""
    for item in reversed(history if isinstance(history, list) else []):
        if (isinstance(item, dict) and item.get("role") == "assistant" and isinstance(item.get("content"), str)
                and item["content"].strip() and "/api/exports/" not in item["content"]):
            text = "\n".join(l for l in item["content"].split("\n") if not l.strip().startswith("_Tip:"))
            return text.strip()
    return None


def _slug(text, fallback):
    lines = [l for l in text.split("\n") if l.strip()]
    first = next((l for l in lines if l.lstrip().startswith("#")), lines[0] if lines else "")
    first = re.sub(r"\(.*?\)", "", first).replace("#", "").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", first).strip("-")
    if len(slug) > 40:
        slug = slug[:40].rsplit("-", 1)[0]
    return slug or fallback


def _a(label):
    return ("an " if label[0] in "AEIOUX" or label in ("HTML",) else "a ") + label


def _files_reply(user_id, content, fmts, slug, intro, key_only=False):
    infos = []
    for fmt in fmts:
        try:
            infos.append(save_export(user_id, content, fmt, slug, key_only=key_only))
        except filetools.FileError as exc:
            raise SpecialError(str(exc))
        except Exception:
            raise SpecialError("I could not build the %s file. Try another format." % filetools.LABELS[fmt])
    links = "\n".join("- [Download %s (%s)](%s)" % (i["filename"], filetools.LABELS[i["format"]], i["url"]) for i in infos)
    return infos, "%s\n\n%s" % (intro, links)


def _intro(fmts, what):
    names = " and ".join(filetools.LABELS[f] for f in fmts)
    return "Done. %s is ready as %s %s file%s." % (what, "an" if names[0] in "AEIOUX" else "a", names, "s" if len(fmts) > 1 else "")


def _domain_report(prompt):
    """-> (label, report) for questions that must be answered with REAL data, else (None, None)."""
    if weather.detect(prompt):
        return "weather", weather.report(prompt)
    asset = marketdata.detect_asset(prompt)
    if asset:
        return asset + " price", marketdata.report(asset, prompt)
    spec = quotes.detect(prompt)
    if spec:
        return "market", quotes.report(spec, prompt)
    return None, None


def handle(user_id, prompt, history):
    """Return {'reply', 'source', 'file', 'files'} when handled here, or None to let the normal AI answer."""
    if is_time_question(prompt):
        return {"reply": now_reply(), "source": "server-clock", "file": None, "files": []}

    label = None
    rep = None
    try:
        if weather.detect(prompt):
            label = "weather"
            rep = weather.report(prompt)
        else:
            asset = marketdata.detect_asset(prompt)
            if asset:
                label = asset + " price"
                rep = marketdata.report(asset, prompt)
            else:
                spec = quotes.detect(prompt)
                if spec:
                    label = "market"
                    rep = quotes.report(spec, prompt)
    except weather.NeedInfo as exc:
        return {"reply": str(exc), "source": "weather-help", "file": None, "files": []}
    except marketdata.DataUnavailable as exc:
        why = "the data service did not respond" if ("error" in str(exc) or "HTTP" in str(exc)) else str(exc)[:160]
        domain_name = label or "market"
        raise SpecialError("I could not get real %s data right now (%s). I will not guess the numbers. "
                           "This request was not counted, please try again in a moment." % (domain_name, why))

    fmts = detect_formats(prompt, anywhere=bool(rep))
    if rep:
        if not fmts:
            return {"reply": rep["markdown"], "source": rep["source"], "file": None, "files": []}
        infos, reply = _files_reply(user_id, rep["markdown"], fmts, rep["slug"], _intro(fmts, "Your real %s data" % (label or "data")))
        return {"reply": reply + "\n\n" + rep["markdown"], "source": rep["source"], "file": infos[0], "files": infos}

    if fmts:
        text = last_exportable(history)
        if not text:
            return None
        infos, reply = _files_reply(user_id, text, fmts, _slug(text, "upian-answer"), _intro(fmts, "Your previous answer"),
                                    key_only=True)
        return {"reply": reply, "source": "file-export", "file": infos[0], "files": infos}
    return None
