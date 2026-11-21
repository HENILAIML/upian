"""Vision router + data extraction/export.

Routing:
  image + labels   -> SigLIP 2 (escalates to Qwen 3B -> Qwen 7B if low confidence)
  image + question -> Qwen 3B (Qwen 7B for hard questions)
  image only       -> Qwen 3B if configured, else SigLIP 2 with built-in labels
Extraction: reads text + tables from an image and writes xlsx/pdf/csv/docx/pptx/json/html/md/txt.

Qwen tiers are called through any OpenAI-compatible API (Ollama, Together, OpenRouter...).
.env settings:
  VISION_QWEN_3B_BASE_URL / VISION_QWEN_3B_MODEL / VISION_QWEN_3B_API_KEY
  VISION_QWEN_7B_BASE_URL / VISION_QWEN_7B_MODEL / VISION_QWEN_7B_API_KEY
  VISION_EXTRACT_BASE_URL / VISION_EXTRACT_MODEL / VISION_EXTRACT_API_KEY  (optional, stronger reader)
  VISION_SIGLIP_MODEL (default google/siglip2-base-patch16-224)
  VISION_CONF_THRESHOLD (default 0.30)
  VISION_OUT_DIR (default vision_outputs)
"""
import base64
import csv
import html as _html
import io
import json
import os
import re
from pathlib import Path

SIGLIP_MODEL = os.getenv("VISION_SIGLIP_MODEL", "google/siglip2-base-patch16-224")
CONF_THRESHOLD = float(os.getenv("VISION_CONF_THRESHOLD", "0.30"))
OUT_DIR = Path(os.getenv("VISION_OUT_DIR", "vision_outputs"))
API_TIMEOUT = int(os.getenv("VISION_API_TIMEOUT", "120"))

DEFAULT_LABELS = [
    "a person", "a cat", "a dog", "a bird", "a car", "a bike", "a building",
    "a tree", "food", "a landscape", "a screenshot", "a document", "a table",
    "an invoice", "a receipt", "a chart", "a logo", "a computer", "a phone",
    "a flower", "water", "a road",
]
DOC_LABELS = ["a table", "an invoice", "a receipt", "a document with text",
              "a spreadsheet", "a printed form", "a screenshot of a table"]
NON_DOC_LABELS = ["a photo of a person", "a photo of an animal", "a landscape",
                  "a photo of food", "a photo of a car", "a photo of a building"]

HARD_PATTERNS = re.compile(
    r"(explain in detail|in detail|why|compare|analy[sz]e|step by step|reason|"
    r"calculate|summari[sz]e the|what would happen|difference between)", re.I)


# ---------------------------------------------------------------- config
def _tier(prefix):
    base = os.getenv(f"{prefix}_BASE_URL", "").rstrip("/")
    model = os.getenv(f"{prefix}_MODEL", "")
    if not base or not model:
        return None
    return {"base": base, "model": model, "key": os.getenv(f"{prefix}_API_KEY", "")}


def tiers():
    return {
        "qwen3b": _tier("VISION_QWEN_3B"),
        "qwen7b": _tier("VISION_QWEN_7B"),
        "extract": _tier("VISION_EXTRACT"),
    }


def is_hard(question):
    return bool(question and (len(question) > 160 or HARD_PATTERNS.search(question)))


# ---------------------------------------------------------------- SigLIP 2
_siglip = None


def _load_siglip():
    global _siglip
    if _siglip is None:
        from transformers import pipeline
        _siglip = pipeline("zero-shot-image-classification", model=SIGLIP_MODEL)
    return _siglip


def classify_siglip(img_bytes, labels):
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    res = _load_siglip()(img, candidate_labels=labels)
    res = sorted(res, key=lambda r: -r["score"])
    return [{"label": r["label"], "score": float(r["score"])} for r in res]


# ---------------------------------------------------------------- Qwen via API
def call_vision_api(tier, prompt, img_bytes, mime="image/png", max_tokens=1500):
    import requests
    b64 = base64.b64encode(img_bytes).decode()
    headers = {"Content-Type": "application/json"}
    if tier.get("key"):
        headers["Authorization"] = f"Bearer {tier['key']}"
    payload = {
        "model": tier["model"],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
    }
    r = requests.post(f"{tier['base']}/chat/completions", headers=headers,
                      json=payload, timeout=API_TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------- router
def route(img_bytes, mime="image/png", labels=None, question=None):
    """Returns dict: answer, model, confidence, labels, path."""
    t = tiers()
    labels = [l.strip() for l in (labels or []) if l and l.strip()]
    question = (question or "").strip()
    path = []

    def ask(tier_name, prompt):
        path.append(tier_name)
        return call_vision_api(t[tier_name], prompt, img_bytes, mime)

    # image + question
    if question:
        order = ["qwen7b", "qwen3b"] if is_hard(question) else ["qwen3b", "qwen7b"]
        for name in order:
            if t[name]:
                return {"answer": ask(name, question), "model": name,
                        "confidence": None, "labels": [], "path": path}
        return {"error": "Questions need a Qwen model. Set VISION_QWEN_3B_BASE_URL and "
                         "VISION_QWEN_3B_MODEL in .env."}

    # image + labels
    if labels:
        ranked = classify_siglip(img_bytes, labels)
        path.append("siglip2")
        top = ranked[0]
        if top["score"] >= CONF_THRESHOLD:
            return {"answer": top["label"], "model": "siglip2",
                    "confidence": top["score"], "labels": ranked, "path": path}
        for name in ("qwen3b", "qwen7b"):
            if t[name]:
                prompt = ("Which ONE of these labels best describes the image? "
                          f"Labels: {', '.join(labels)}. Reply with only the label.")
                ans = ask(name, prompt)
                return {"answer": ans, "model": name, "confidence": None,
                        "labels": ranked, "path": path}
        return {"answer": top["label"], "model": "siglip2",
                "confidence": top["score"], "labels": ranked, "path": path,
                "note": "Low confidence and no Qwen tier configured."}

    # image only
    if t["qwen3b"]:
        return {"answer": ask("qwen3b", "Describe this image briefly and list the main objects."),
                "model": "qwen3b", "confidence": None, "labels": [], "path": path}
    ranked = classify_siglip(img_bytes, DEFAULT_LABELS)
    path.append("siglip2")
    return {"answer": ranked[0]["label"], "model": "siglip2",
            "confidence": ranked[0]["score"], "labels": ranked[:5], "path": path}


def looks_like_document(img_bytes):
    """True if the image looks like a table/invoice/document (used for auto-extract)."""
    try:
        ranked = classify_siglip(img_bytes, DOC_LABELS + NON_DOC_LABELS)
    except Exception:
        return False
    top = ranked[0]
    return top["label"] in DOC_LABELS and top["score"] >= CONF_THRESHOLD


# ---------------------------------------------------------------- extraction
EXTRACT_PROMPT = """Read this image carefully and extract ALL text and ALL tables exactly as shown.
Rules:
- Do not invent or guess values. If something is unreadable write [unclear].
- Keep numbers, dates, currency symbols and units exactly as printed.
- Return ONLY valid JSON, no explanation, in this shape:
{"title": "short title or empty", "text": "all non-table text, line breaks as \\n",
 "tables": [{"name": "table name", "rows": [["header1","header2"],["cell","cell"]]}]}
If there are no tables use an empty list."""


def _parse_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def extract_data(img_bytes, mime="image/png"):
    t = tiers()
    tier = t["extract"] or t["qwen7b"] or t["qwen3b"]
    if not tier:
        raise RuntimeError("Reading data from images needs a vision model. Set "
                           "VISION_QWEN_3B_* (or VISION_EXTRACT_*) in .env.")
    raw = call_vision_api(tier, EXTRACT_PROMPT, img_bytes, mime, max_tokens=4000)
    data = _parse_json(raw)
    if not isinstance(data, dict):
        data = {"title": "", "text": raw, "tables": []}
    tables = []
    for i, tb in enumerate(data.get("tables") or [], 1):
        rows = [["" if c is None else str(c) for c in row] for row in (tb.get("rows") or [])]
        if rows:
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            tables.append({"name": str(tb.get("name") or f"Table {i}"), "rows": rows})
    return {"title": str(data.get("title") or ""), "text": str(data.get("text") or ""),
            "tables": tables}


# ---------------------------------------------------------------- exporters
FORMATS = ["xlsx", "pdf", "csv", "docx", "pptx", "json", "html", "md", "txt"]


def _sheet_name(name, used):
    base = re.sub(r"[\[\]\*\?/\\:]", " ", name)[:28].strip() or "Table"
    n, i = base, 2
    while n in used:
        n = f"{base[:25]}_{i}"
        i += 1
    used.add(n)
    return n


def _x_csv(d, p):
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for tb in d["tables"]:
            if len(d["tables"]) > 1:
                w.writerow([tb["name"]])
            w.writerows(tb["rows"])
            w.writerow([])
        if not d["tables"]:
            for line in d["text"].splitlines():
                w.writerow([line])


def _x_xlsx(d, p):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for tb in d["tables"]:
        ws = wb.create_sheet(_sheet_name(tb["name"], used))
        for row in tb["rows"]:
            ws.append(row)
        for c in ws[1]:
            c.font = Font(bold=True)
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)
    if d["text"] or not d["tables"]:
        ws = wb.create_sheet(_sheet_name("Text", used))
        for line in (d["text"] or "").splitlines() or [""]:
            ws.append([line])
    wb.save(p)


def _x_pdf(d, p):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    st = getSampleStyleSheet()
    esc = lambda s: _html.escape(s).replace("\n", "<br/>")
    doc = SimpleDocTemplate(str(p), pagesize=landscape(A4) if any(
        len(t["rows"][0]) > 5 for t in d["tables"]) else A4)
    story = [Paragraph(esc(d["title"] or "Extracted data"), st["Title"])]
    if d["text"]:
        story += [Paragraph(esc(d["text"]), st["BodyText"]), Spacer(1, 12)]
    for tb in d["tables"]:
        story.append(Paragraph(esc(tb["name"]), st["Heading3"]))
        data = [[Paragraph(esc(c), st["BodyText"]) for c in r] for r in tb["rows"]]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [t, Spacer(1, 14)]
    doc.build(story)


def _x_docx(d, p):
    from docx import Document
    doc = Document()
    doc.add_heading(d["title"] or "Extracted data", 1)
    if d["text"]:
        for line in d["text"].splitlines():
            doc.add_paragraph(line)
    for tb in d["tables"]:
        doc.add_heading(tb["name"], 3)
        t = doc.add_table(rows=len(tb["rows"]), cols=len(tb["rows"][0]))
        t.style = "Table Grid"
        for i, row in enumerate(tb["rows"]):
            for j, c in enumerate(row):
                t.cell(i, j).text = c
    doc.save(p)


def _x_pptx(d, p):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = d["title"] or "Extracted data"
    if d["text"]:
        tb = s.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(9), Inches(5))
        tb.text_frame.word_wrap = True
        tb.text_frame.text = d["text"][:1500]
    for tbl in d["tables"]:
        for start in range(0, len(tbl["rows"]), 14):
            rows = tbl["rows"][start:start + 14]
            s = prs.slides.add_slide(prs.slide_layouts[5])
            s.shapes.title.text = tbl["name"]
            shape = s.shapes.add_table(len(rows), len(rows[0]), Inches(0.4), Inches(1.5),
                                       Inches(9.2), Inches(0.35) * len(rows))
            for i, row in enumerate(rows):
                for j, c in enumerate(row):
                    cell = shape.table.cell(i, j)
                    cell.text = c
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(10)
    prs.save(p)


def _x_json(d, p):
    Path(p).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def _x_html(d, p):
    e = _html.escape
    parts = ["<!doctype html><meta charset='utf-8'><style>body{font-family:sans-serif;margin:2rem}"
             "table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #999;padding:4px 8px}"
             "tr:first-child td{background:#eee;font-weight:bold}</style>",
             f"<h1>{e(d['title'] or 'Extracted data')}</h1>"]
    if d["text"]:
        parts.append(f"<p>{e(d['text']).replace(chr(10), '<br>')}</p>")
    for tb in d["tables"]:
        parts.append(f"<h3>{e(tb['name'])}</h3><table>")
        for r in tb["rows"]:
            parts.append("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>")
        parts.append("</table>")
    Path(p).write_text("".join(parts), encoding="utf-8")


def _x_md(d, p):
    out = [f"# {d['title'] or 'Extracted data'}", ""]
    if d["text"]:
        out += [d["text"], ""]
    for tb in d["tables"]:
        out += [f"## {tb['name']}", ""]
        rows = [[c.replace("|", "\\|").replace("\n", " ") for c in r] for r in tb["rows"]]
        out.append("| " + " | ".join(rows[0]) + " |")
        out.append("|" + "---|" * len(rows[0]))
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        out.append("")
    Path(p).write_text("\n".join(out), encoding="utf-8")


def _x_txt(d, p):
    out = [d["title"], ""] if d["title"] else []
    if d["text"]:
        out += [d["text"], ""]
    for tb in d["tables"]:
        out.append(tb["name"])
        out += ["\t".join(r) for r in tb["rows"]]
        out.append("")
    Path(p).write_text("\n".join(out), encoding="utf-8")


_EXPORTERS = {"csv": _x_csv, "xlsx": _x_xlsx, "pdf": _x_pdf, "docx": _x_docx,
              "pptx": _x_pptx, "json": _x_json, "html": _x_html, "md": _x_md, "txt": _x_txt}


def export_files(data, formats, job_id):
    """Write the chosen formats. Returns (files, errors)."""
    folder = OUT_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    files, errors = [], {}
    for fmt in formats:
        if fmt not in _EXPORTERS:
            continue
        name = f"extracted.{fmt}"
        try:
            _EXPORTERS[fmt](data, folder / name)
            files.append(name)
        except ImportError as e:
            errors[fmt] = f"missing package: {e.name}"
        except Exception as e:  # keep other formats working
            errors[fmt] = str(e)
    return files, errors
