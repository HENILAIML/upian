"""Chat shortcuts that must NOT be answered by a language model:
  1. "gold price 2020 to 2030"  -> real market data, returned as a table (never invented by the AI)
  2. "give me that in excel/pdf/word/csv/ppt/json/html/txt/md" -> the previous answer turned into a real file
Both can be combined: "gold price last 10 years in excel".
"""
import re

import filetools
import marketdata
from file_routes import save_export

TIP = "_Tip: tell me the format you want (Excel, PDF, Word, CSV, PowerPoint, JSON, HTML, TXT, MD) and I will give you the file._"


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


def detect_format(prompt, anywhere=False):
    """Which file format does the user want? None when the prompt is not an export request."""
    p = prompt.lower().strip()
    if not anywhere and (len(p.split()) > 14 or _QUESTION.match(p) or _CODING.search(p)):
        return None
    for fmt, (strong, weak) in FORMATS.items():
        alts = "|".join(x for x in (strong, weak) if x)
        if re.search(_PREP + r"(?:" + alts + r")\b", p):
            return fmt
        if re.search(r"\b(?:" + alts + r")\s+(?:file|format|document|version)\b", p):
            return fmt
        if strong and (anywhere or len(p.split()) <= 4) and re.search(r"\b(?:" + strong + r")\b", p):
            return fmt
    return None


def last_exportable(history):
    """Text of the newest assistant answer that is not itself a 'here is your file' message."""
    for item in reversed(history if isinstance(history, list) else []):
        if (isinstance(item, dict) and item.get("role") == "assistant" and isinstance(item.get("content"), str)
                and item["content"].strip() and "/api/exports/" not in item["content"]):
            text = "\n".join(l for l in item["content"].split("\n") if not l.strip().startswith("_Tip:"))
            return text.strip()
    return None


def _slug(text, fallback):
    first = next((l for l in text.split("\n") if l.strip()), "")
    first = re.sub(r"\(.*?\)", "", first).replace("#", "").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", first).strip("-")
    if len(slug) > 40:
        slug = slug[:40].rsplit("-", 1)[0]
    return slug or fallback


def _a(label):
    return ("an " if label[0] in "AEIOUX" or label in ("HTML",) else "a ") + label


def _file_reply(user_id, content, fmt, slug, intro):
    try:
        info = save_export(user_id, content, fmt, slug)
    except filetools.FileError as exc:
        raise SpecialError(str(exc))
    except Exception:
        raise SpecialError("I could not build the %s file. Try another format." % filetools.LABELS[fmt])
    link = "[Download %s](%s)" % (info["filename"], info["url"])
    return info, "%s\n\n%s" % (intro, link)


def handle(user_id, prompt, history):
    """Return {'reply', 'source', 'file'} when handled here, or None to let the normal AI answer."""
    asset = marketdata.detect_asset(prompt)
    fmt = detect_format(prompt, anywhere=bool(asset))

    if asset:
        try:
            rep = marketdata.report(asset, prompt)
        except marketdata.DataUnavailable as exc:
            raise SpecialError(
                "I could not get real %s price data right now (%s). I will not guess the numbers. "
                "This request was not counted, please try again in a moment."
                % (asset, "the price service did not respond" if "error" in str(exc) or "HTTP" in str(exc) else str(exc)[:150]))
        if not fmt:
            return {"reply": rep["markdown"] + "\n\n" + TIP, "source": rep["source"], "file": None}
        info, reply = _file_reply(user_id, rep["markdown"], fmt, rep["slug"],
                                  "Here is the real %s price data as %s file." % (asset, _a(filetools.LABELS[fmt])))
        return {"reply": reply + "\n\n" + rep["markdown"], "source": rep["source"], "file": info}

    if fmt:
        text = last_exportable(history)
        if not text:
            return None
        info, reply = _file_reply(user_id, text, fmt, _slug(text, "upian-answer"),
                                  "Done. Your previous answer is ready as %s file." % _a(filetools.LABELS[fmt]))
        return {"reply": reply, "source": "file-export", "file": info}
    return None
