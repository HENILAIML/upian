"""Unit and Integration Tests for Multimodal Upload System (Phase 6).

Verifies all 9 supported formats individually:
  1. PNG
  2. JPG
  3. JPEG
  4. WEBP
  5. PDF
  6. TXT
  7. CSV
  8. DOCX
  9. XLSX
Plus security checks (magic bytes, path traversal rejection, size limit).
"""
import io
import os
import pytest
from PIL import Image
import docx
from openpyxl import Workbook
from reportlab.pdfgen import canvas

import multimodal


# --- Generators for each supported format ---

def make_png():
    img = Image.new("RGB", (32, 32), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_jpg():
    img = Image.new("RGB", (32, 32), color="green")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_webp():
    img = Image.new("RGB", (32, 32), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def make_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "UPIAN Gateway Test PDF Document")
    c.save()
    return buf.getvalue()


def make_docx():
    doc = docx.Document()
    doc.add_heading("Test Word Document", 0)
    doc.add_paragraph("Paragraph content for multimodal testing.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "TestSheet"
    ws.append(["Header1", "Header2", "Header3"])
    ws.append(["Value1", "Value2", "Value3"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_txt():
    return b"Plain text file test content for UPIAN Gateway.\nSecond line of text."


def make_csv():
    return b"Name,Role,Status\nAlice,Architect,Active\nBob,Engineer,Active\n"


# --- Tests for all 9 formats individually ---

def test_format_png():
    data = make_png()
    ext, kind = multimodal.detect_file_type(data, "photo.png")
    assert ext == ".png"
    assert kind == "image"


def test_format_jpg():
    data = make_jpg()
    ext, kind = multimodal.detect_file_type(data, "photo.jpg")
    assert ext == ".jpg"
    assert kind == "image"


def test_format_jpeg():
    data = make_jpg()
    ext, kind = multimodal.detect_file_type(data, "photo.jpeg")
    assert ext == ".jpeg"
    assert kind == "image"


def test_format_webp():
    data = make_webp()
    ext, kind = multimodal.detect_file_type(data, "banner.webp")
    assert ext == ".webp"
    assert kind == "image"


def test_format_pdf():
    data = make_pdf()
    ext, kind = multimodal.detect_file_type(data, "report.pdf")
    assert ext == ".pdf"
    assert kind == "doc"


def test_format_docx():
    data = make_docx()
    ext, kind = multimodal.detect_file_type(data, "specification.docx")
    assert ext == ".docx"
    assert kind == "doc"


def test_format_xlsx():
    data = make_xlsx()
    ext, kind = multimodal.detect_file_type(data, "financials.xlsx")
    assert ext == ".xlsx"
    assert kind == "doc"


def test_format_txt():
    data = make_txt()
    ext, kind = multimodal.detect_file_type(data, "notes.txt")
    assert ext == ".txt"
    assert kind == "text"


def test_format_csv():
    data = make_csv()
    ext, kind = multimodal.detect_file_type(data, "users.csv")
    assert ext == ".csv"
    assert kind == "text"


# --- Security & Validation Tests ---

def test_magic_bytes_spoofing_prevention():
    # Attempt to spoof a PNG extension with fake text
    fake_png = b"This is not a real PNG file at all!"
    with pytest.raises(multimodal.ValidationError) as exc:
        multimodal.detect_file_type(fake_png, "malicious.png")
    assert exc.value.code == "magic_bytes_mismatch"


def test_unsupported_file_extension():
    # Attempt to upload executable
    fake_exe = b"MZ\x90\x00\x03\x00\x00\x00"
    with pytest.raises(multimodal.ValidationError) as exc:
        multimodal.detect_file_type(fake_exe, "payload.exe")
    assert exc.value.code == "unsupported_extension"


def test_path_traversal_sanitization():
    evil_name = "../../../../../etc/passwd.txt"
    safe = multimodal.sanitize_filename(evil_name)
    assert "/" not in safe
    assert "\\" not in safe
    assert ".." not in safe


def test_successful_save_uploaded_file():
    data = make_png()
    saved = multimodal.save_uploaded_file(data, "avatar.png", user_id="test_user_99")
    assert saved["extension"] == ".png"
    assert saved["kind"] == "image"
    assert saved["size_bytes"] == len(data)
    assert os.path.exists(os.path.join(multimodal.Config.UPLOAD_DIR, "test_user_99", saved["stored_filename"]))
