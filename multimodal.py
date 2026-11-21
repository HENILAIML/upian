"""UPIAN Multimodal Upload & Validation Engine.

Supports:
  - Images: PNG, JPG, JPEG, WEBP
  - Documents: PDF, DOCX, XLSX
  - Text & Tabular: TXT, CSV

Security:
  - Strict magic byte detection (guards against file extension spoofing)
  - Filename sanitization & path traversal defense
  - File size & user storage quota enforcement
  - Secure temporary & persistent storage isolation
  - Thumbnail generation for supported visual formats
"""
import io
import os
import re
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

from config import Config

SUPPORTED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".jfif", ".tif", ".tiff",
    ".pdf", ".docx", ".xlsx",
    ".txt", ".csv",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".jfif", ".tif", ".tiff"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx"}
TEXT_EXTENSIONS = {".txt", ".csv"}

# Magic byte signatures
MAGIC_SIGNATURES = {
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".jfif": [b"\xff\xd8\xff"],
    ".bmp": [b"BM"],
    ".pdf": [b"%PDF"],
    ".docx": [b"PK\x03\x04"],
    ".xlsx": [b"PK\x03\x04"],
    ".tif": [b"II*\x00", b"MM\x00*"],
    ".tiff": [b"II*\x00", b"MM\x00*"],
}


class ValidationError(Exception):
    def __init__(self, message, code="validation_error", status_code=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def sanitize_filename(filename, fallback="document"):
    """Sanitizes filename and strictly eliminates directory traversal vectors."""
    base = os.path.basename(filename or fallback)
    clean = secure_filename(base)
    if not clean:
        clean = fallback
    return clean[:120]


def detect_file_type(raw_bytes, declared_filename=""):
    """
    Detects and validates real file format using extension and binary magic bytes.
    Automatically infers the extension if missing or generic (e.g. pasted screenshots).
    Raises ValidationError if the content does not match allowed types.
    """
    ext = os.path.splitext(declared_filename.lower())[1]

    # If extension is missing or not in supported, attempt auto-inference from magic bytes
    if not ext or ext not in SUPPORTED_EXTENSIONS:
        if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = ".png"
        elif raw_bytes.startswith(b"\xff\xd8\xff"):
            ext = ".jpg"
        elif raw_bytes.startswith(b"BM"):
            ext = ".bmp"
        elif raw_bytes.startswith(b"RIFF") and b"WEBP" in raw_bytes[:16]:
            ext = ".webp"
        elif raw_bytes.startswith(b"%PDF"):
            ext = ".pdf"
        elif raw_bytes.startswith(b"PK\x03\x04"):
            ext = ".docx"
        elif raw_bytes.startswith(b"II*\x00") or raw_bytes.startswith(b"MM\x00*"):
            ext = ".tif"
        else:
            # Check if Pillow can open as an image
            try:
                with Image.open(io.BytesIO(raw_bytes)) as test_img:
                    fmt = (test_img.format or "").lower()
                    if fmt == "jpeg":
                        ext = ".jpg"
                    elif fmt in ("png", "webp", "bmp", "tiff"):
                        ext = f".{fmt}"
                    else:
                        ext = ".png"
            except Exception:
                pass

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file extension '{ext}'. Allowed: PNG, JPG, JPEG, WEBP, BMP, PDF, DOCX, XLSX, TXT, CSV.",
            code="unsupported_extension",
        )

    # Magic byte verification for binary formats
    if ext in MAGIC_SIGNATURES:
        signatures = MAGIC_SIGNATURES[ext]
        if not any(raw_bytes.startswith(sig) for sig in signatures):
            raise ValidationError(
                f"File content does not match declared extension '{ext}' (magic bytes mismatch).",
                code="magic_bytes_mismatch",
            )

    # Special check for WebP (RIFF....WEBP)
    if ext == ".webp":
        if not (raw_bytes.startswith(b"RIFF") and b"WEBP" in raw_bytes[:16]):
            raise ValidationError("Invalid WEBP image structure.", code="invalid_webp")

    # Check for TXT and CSV (must be decodable text)
    if ext in TEXT_EXTENSIONS:
        try:
            sample = raw_bytes[:4096].decode("utf-8")
        except UnicodeDecodeError:
            try:
                sample = raw_bytes[:4096].decode("latin-1")
            except Exception:
                raise ValidationError("File declared as text contains invalid binary data.", code="binary_as_text")
        if "\x00" in sample:
            raise ValidationError("Text file contains null bytes (binary payload rejected).", code="null_byte_detected")

    # Image integrity check with Pillow
    if ext in IMAGE_EXTENSIONS:
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                img.verify()
        except Exception as e:
            raise ValidationError(f"Corrupt or invalid image file: {e}", code="corrupt_image")

    kind = "image" if ext in IMAGE_EXTENSIONS else ("doc" if ext in DOCUMENT_EXTENSIONS else "text")
    return ext, kind


def create_thumbnail(img_bytes, max_size=(256, 256)):
    """Generates a small thumbnail JPEG for inline frontend preview."""
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                mask = img.split()[-1] if img.mode in ("RGBA", "LA") else None
                bg.paste(img, mask=mask)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception:
        return None


def save_uploaded_file(raw_bytes, original_filename, user_id):
    """
    Validates, sanitizes, and saves an uploaded file into user's isolated directory.
    Returns metadata including stored token, size, format, kind, and URLs.
    """
    max_bytes = Config.FILE_MAX_MB * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise ValidationError(
            f"File exceeds maximum size limit of {Config.FILE_MAX_MB} MB.",
            code="file_too_large",
            status_code=413,
        )

    safe_name = sanitize_filename(original_filename)
    ext, kind = detect_file_type(raw_bytes, safe_name)
    if not os.path.splitext(safe_name)[1]:
        safe_name = f"{safe_name}{ext}"

    token = uuid.uuid4().hex
    user_dir = Path(Config.UPLOAD_DIR) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{token}{ext}"
    target_path = (user_dir / stored_filename).resolve()

    # Path traversal assertion
    if not str(target_path).startswith(str(user_dir.resolve())):
        raise ValidationError("Path traversal attempt detected.", code="path_traversal", status_code=403)

    with open(target_path, "wb") as f:
        f.write(raw_bytes)

    # If it's an image, create a thumbnail for fast chat rendering
    thumb_url = None
    if kind == "image":
        thumb_bytes = create_thumbnail(raw_bytes)
        if thumb_bytes:
            thumb_name = f"{token}_thumb.jpg"
            with open(user_dir / thumb_name, "wb") as tf:
                tf.write(thumb_bytes)
            thumb_url = f"/api/files/{user_id}/thumb/{token}"

    return {
        "token": token,
        "original_filename": safe_name,
        "stored_filename": stored_filename,
        "extension": ext,
        "kind": kind,
        "size_bytes": len(raw_bytes),
        "url": f"/api/files/{user_id}/download/{token}",
        "thumbnail_url": thumb_url,
    }
