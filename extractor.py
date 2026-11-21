"""UPIAN Structured Document & Data Extraction Engine.

Extracts text, detects tables, preserves headers and rows, generates summaries,
and exports into all 6 downloadable formats:
  - PDF
  - DOCX
  - XLSX
  - CSV
  - TXT
  - JSON
"""
import csv
import io
import json
import os
import re
from pathlib import Path

import vision
from multimodal import detect_file_type


def extract_from_document(file_bytes, ext):
    """Extracts text and structured tables from document formats (PDF, DOCX, XLSX, CSV, TXT)."""
    ext = ext.lower().strip()
    title = f"Extracted Document ({ext.upper().strip('.')})"
    text_parts = []
    tables = []

    if ext == ".csv":
        # Parse CSV into structured table
        try:
            content = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = file_bytes.decode("latin-1", errors="replace")
        reader = csv.reader(io.StringIO(content))
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if rows:
            tables.append({"name": "Data Table", "rows": rows})
            text_parts.append(f"Extracted CSV containing {len(rows)} rows.")

    elif ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        for ws in wb.worksheets:
            sheet_rows = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if any(cells):
                    sheet_rows.append(cells)
            if sheet_rows:
                tables.append({"name": f"Sheet - {ws.title}", "rows": sheet_rows})
                text_parts.append(f"Sheet '{ws.title}': {len(sheet_rows)} rows extracted.")

    elif ext == ".docx":
        import docx
        doc = docx.Document(io.BytesIO(file_bytes))
        for p in doc.paragraphs:
            if p.text.strip():
                text_parts.append(p.text)
        for i, t in enumerate(doc.tables, 1):
            table_rows = []
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                table_rows.append(cells)
            if table_rows:
                tables.append({"name": f"Table {i}", "rows": table_rows})

    elif ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        page_texts = []
        for idx, page in enumerate(reader.pages, 1):
            t = page.extract_text() or ""
            if t.strip():
                page_texts.append(f"--- Page {idx} ---\n{t.strip()}")
        text_parts.append("\n\n".join(page_texts))

    elif ext == ".txt":
        try:
            content = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = file_bytes.decode("latin-1", errors="replace")
        text_parts.append(content)

    full_text = "\n\n".join(text_parts).strip()
    summary = ""
    if full_text:
        first_few = [l.strip() for l in full_text.splitlines() if l.strip()]
        if first_few:
            summary = " ".join(first_few[:3])[:300]

    return {
        "title": title,
        "summary": summary,
        "text": full_text,
        "tables": tables,
    }


def extract_from_file_or_image(file_bytes, filename):
    """
    Unified extraction router:
    Accepts any supported file (PDF, DOCX, XLSX, TXT, CSV, PNG, JPG, JPEG, WEBP).
    """
    ext, kind = detect_file_type(file_bytes, filename)
    if kind == "image":
        mime = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
        data = vision.extract_data(file_bytes, mime=mime)
        return data, ext, kind
    else:
        data = extract_from_document(file_bytes, ext)
        return data, ext, kind


def export_all_formats(extracted_data, output_dir, base_name="extracted"):
    """
    Builds real, valid downloadable files in all 6 required formats:
    PDF, DOCX, XLSX, CSV, TXT, JSON.
    Returns dict mapping format name to absolute file path.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    generated = {}

    for fmt in ("pdf", "docx", "xlsx", "csv", "txt", "json"):
        filepath = out_path / f"{base_name}.{fmt}"
        fn = vision._EXPORTERS.get(fmt)
        if fn:
            fn(extracted_data, filepath)
            if filepath.exists() and filepath.stat().st_size > 0:
                generated[fmt] = str(filepath)

    return generated
