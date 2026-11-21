"""Unit and Integration Tests for Document and Data Extraction & Exporters (Phase 7).

Verifies:
  1. Text & Table Extraction from files.
  2. Table headers and rows preservation.
  3. Generating all 6 downloadable formats: PDF, DOCX, XLSX, CSV, TXT, JSON.
  4. Opening each generated file with native library to confirm integrity and contents.
"""
import csv
import json
import os
import io
import pytest
import docx
from openpyxl import load_workbook
from pypdf import PdfReader

import extractor
import vision


@pytest.fixture
def sample_structured_data():
    return {
        "title": "UPIAN Multi-Cloud Performance Report",
        "summary": "Benchmarking data comparing response latencies across three AI providers.",
        "text": "Comprehensive analysis of API latency, token throughput, and routing accuracy.",
        "tables": [
            {
                "name": "Provider Latency Matrix",
                "rows": [
                    ["Provider", "Tier", "p50 Latency (ms)", "p95 Latency (ms)", "Availability"],
                    ["Google Gemini", "Fast", "320", "640", "99.98%"],
                    ["Qwen Vision", "Local", "180", "410", "99.95%"],
                    ["OpenAI DALL-E", "Image", "1850", "3200", "99.90%"],
                ],
            }
        ],
    }


def test_csv_extraction():
    csv_raw = b"Metric,Q1,Q2\nLatency,120ms,95ms\nAccuracy,98%,99%\n"
    res = extractor.extract_from_document(csv_raw, ".csv")
    assert len(res["tables"]) == 1
    table = res["tables"][0]
    assert table["rows"][0] == ["Metric", "Q1", "Q2"]
    assert table["rows"][1] == ["Latency", "120ms", "95ms"]


def test_export_and_file_integrity_all_6_formats(tmp_path, sample_structured_data):
    out_dir = tmp_path / "exports"
    files = extractor.export_all_formats(sample_structured_data, out_dir, "benchmark_report")

    # Verify all 6 formats are generated
    assert set(files.keys()) == {"pdf", "docx", "xlsx", "csv", "txt", "json"}

    # 1. Verify CSV opens and contains table headers & data
    with open(files["csv"], "r", encoding="utf-8-sig") as f:
        r = list(csv.reader(f))
        headers_found = any("Provider" in row and "Tier" in row for row in r)
        assert headers_found, "CSV must contain table headers"

    # 2. Verify XLSX opens and contains sheet & cells
    wb = load_workbook(files["xlsx"])
    assert len(wb.worksheets) >= 1
    ws = wb.worksheets[0]
    assert ws.cell(row=1, column=1).value == "Provider"
    assert ws.cell(row=2, column=1).value == "Google Gemini"

    # 3. Verify DOCX opens and contains tables & title
    doc = docx.Document(files["docx"])
    assert len(doc.tables) >= 1
    first_table = doc.tables[0]
    assert first_table.cell(0, 0).text == "Provider"
    assert first_table.cell(1, 0).text == "Google Gemini"

    # 4. Verify PDF opens and has at least 1 readable page
    reader = PdfReader(files["pdf"])
    assert len(reader.pages) >= 1
    pdf_text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "UPIAN" in pdf_text or "Report" in pdf_text or "Provider" in pdf_text

    # 5. Verify JSON opens and matches structured schema
    with open(files["json"], "r", encoding="utf-8") as f:
        j = json.load(f)
        assert j["title"] == sample_structured_data["title"]
        assert len(j["tables"]) == 1
        assert j["tables"][0]["rows"][0][0] == "Provider"

    # 6. Verify TXT opens and contains ASCII formatted table
    with open(files["txt"], "r", encoding="utf-8") as f:
        txt = f.read()
        assert "Provider" in txt
        assert "Google Gemini" in txt
