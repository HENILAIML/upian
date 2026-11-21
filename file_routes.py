"""Pages and API: upload, list, download, delete, create-file."""
import io
import os
import re
import time
import uuid

from flask import abort, g, jsonify, render_template, request, send_file
from sqlalchemy import func
from werkzeug.utils import secure_filename

import filetools
from config import Config
from models import Document, db, utcnow


EXPORT_TTL = 24 * 3600  # generated files are kept for one day
_EXPORT_NAME = re.compile(r"^[0-9a-f]{32}\.(?:%s)$" % "|".join(filetools.CREATE_FORMATS))


def _clean_name(name, fallback="upian-answer"):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(name or fallback))[:50].strip("-") or fallback


def save_export(user_id, content, fmt, name, key_only=False):
    """Build a file from `content` and store it for the user. Returns {format, filename, url, size}."""
    blob, _mime, ext = filetools.make_file(content, fmt, key_only=key_only)
    folder = os.path.join(Config.UPLOAD_DIR, str(user_id), "exports")
    os.makedirs(folder, exist_ok=True)
    now = time.time()
    for old in os.listdir(folder):  # tidy up old exports
        p = os.path.join(folder, old)
        if now - os.path.getmtime(p) > EXPORT_TTL:
            try:
                os.remove(p)
            except OSError:
                pass
    token = uuid.uuid4().hex + "." + ext
    with open(os.path.join(folder, token), "wb") as fh:
        fh.write(blob)
    filename = "%s.%s" % (_clean_name(name), ext)
    return {"format": ext, "filename": filename, "size": len(blob), "url": "/api/exports/%s?name=%s" % (token, filename)}


def save_images_pdf(user_id, image_paths, name="collected-screenshots"):
    """Compile multiple image files into a single high-quality PDF and save to user exports."""
    blob = filetools.compile_images_to_pdf(image_paths)
    folder = os.path.join(Config.UPLOAD_DIR, str(user_id), "exports")
    os.makedirs(folder, exist_ok=True)
    now = time.time()
    for old in os.listdir(folder):  # tidy up old exports
        p = os.path.join(folder, old)
        if now - os.path.getmtime(p) > EXPORT_TTL:
            try:
                os.remove(p)
            except OSError:
                pass
    token = uuid.uuid4().hex + ".pdf"
    with open(os.path.join(folder, token), "wb") as fh:
        fh.write(blob)
    filename = "%s.pdf" % _clean_name(name, fallback="collected-screenshots")
    return {
        "format": "pdf",
        "filename": filename,
        "size": len(blob),
        "url": "/api/exports/%s?name=%s" % (token, filename)
    }


def register_files(app, login_required):
    def user_dir(uid):
        d = os.path.join(Config.UPLOAD_DIR, str(uid))
        os.makedirs(d, exist_ok=True)
        return d

    def own_doc(doc_id, uid):
        doc = db.session.get(Document, doc_id)
        if not doc or doc.user_id != uid:
            abort(404, description="File not found.")
        return doc

    def remove_files(doc):
        base = os.path.join(user_dir(doc.user_id), doc.stored_name)
        for p in (base, base + ".txt"):
            if os.path.exists(p):
                os.remove(p)

    @app.get("/files")
    @login_required
    def files_page():
        used = db.session.query(func.coalesce(func.sum(Document.size), 0)).filter_by(user_id=g.user.id).scalar()
        boot = {"max_mb": Config.FILE_MAX_MB, "allowed": sorted(filetools.ALLOWED_EXT)}
        return render_template("files.html", boot=boot, used_mb=round(used / 1048576, 1),
                               quota_mb=Config.FILE_USER_QUOTA_MB, max_files=Config.FILE_MAX_COUNT)

    @app.get("/api/docs")
    @login_required
    def api_docs():
        rows = Document.query.filter_by(user_id=g.user.id).order_by(Document.id.desc()).all()
        return jsonify(items=[d.to_dict() for d in rows])

    @app.post("/api/docs")
    @login_required
    def api_upload():
        user = g.user
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify(error="Choose a file first."), 400
        name = secure_filename(f.filename) or "file"
        ext = os.path.splitext(name)[1].lower()
        if ext in filetools.BLOCKED_EXT:
            return jsonify(error="This file type is not allowed."), 400
        if ext not in filetools.ALLOWED_EXT:
            return jsonify(error="Unsupported type '%s'. Allowed: PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, JSON, HTML and code files." % (ext or "none")), 400
        count = Document.query.filter_by(user_id=user.id).count()
        used = db.session.query(func.coalesce(func.sum(Document.size), 0)).filter_by(user_id=user.id).scalar()
        if count >= Config.FILE_MAX_COUNT:
            return jsonify(error="You reached the limit of %d files. Delete one first." % Config.FILE_MAX_COUNT), 400

        stored = uuid.uuid4().hex + ext
        path = os.path.join(user_dir(user.id), stored)
        f.save(path)
        size = os.path.getsize(path)

        def fail(msg, code=400):
            for p in (path, path + ".txt"):
                if os.path.exists(p):
                    os.remove(p)
            return jsonify(error=msg), code

        if size == 0:
            return fail("The file is empty.")
        if size > Config.FILE_MAX_MB * 1048576:
            return fail("File is too big. The limit is %d MB." % Config.FILE_MAX_MB)
        if used + size > Config.FILE_USER_QUOTA_MB * 1048576:
            return fail("Not enough storage. Your limit is %d MB." % Config.FILE_USER_QUOTA_MB)
        import hashlib
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        dup = Document.query.filter_by(user_id=user.id, file_hash=digest).first()
        if dup:
            return fail("You already uploaded this file as '%s'." % dup.filename, 409)
        try:
            text, pages = filetools.extract_text(path, ext)
        except filetools.FileError as exc:
            return fail(str(exc))
        except Exception:
            app.logger.exception("reading file failed")
            return fail("Could not read this file.", 500)
        with open(path + ".txt", "w", encoding="utf-8") as fh:
            fh.write(text)
        doc = Document(user_id=user.id, filename=name, stored_name=stored, ext=ext, size=size,
                       file_hash=digest, chars=len(text), pages=pages)
        db.session.add(doc)
        db.session.commit()
        return jsonify(item=doc.to_dict())

    @app.get("/api/docs/<int:doc_id>/download")
    @login_required
    def api_download(doc_id):
        doc = own_doc(doc_id, g.user.id)
        path = os.path.join(user_dir(g.user.id), doc.stored_name)
        if not os.path.exists(path):
            abort(404, description="The original file is missing on the server.")
        return send_file(path, as_attachment=True, download_name=doc.filename)

    @app.delete("/api/docs/<int:doc_id>")
    @login_required
    def api_delete(doc_id):
        doc = own_doc(doc_id, g.user.id)
        remove_files(doc)
        db.session.delete(doc)
        db.session.commit()
        return jsonify(ok=True)

    @app.get("/api/exports/<token>")
    @login_required
    def api_export_download(token):
        if not _EXPORT_NAME.match(token):
            abort(404, description="File not found.")
        path = os.path.join(Config.UPLOAD_DIR, str(g.user.id), "exports", token)  # per-user folder = ownership check
        if not os.path.exists(path):
            abort(404, description="This file expired. Ask for it again.")
        ext = token.rsplit(".", 1)[1]
        name = "%s.%s" % (_clean_name(os.path.splitext(request.args.get("name", ""))[0]), ext)
        return send_file(path, mimetype=filetools.MIME[ext], as_attachment=True, download_name=name)

    @app.post("/api/create-file")
    @login_required
    def api_create_file():
        data = request.get_json(silent=True) or {}
        content, fmt = data.get("content"), str(data.get("format") or "").lower()
        if not isinstance(content, str) or not content.strip():
            return jsonify(error="Nothing to save."), 400
        if fmt not in filetools.CREATE_FORMATS:
            return jsonify(error="Choose one of: " + ", ".join(filetools.CREATE_FORMATS)), 400
        if len(content) > 200000:
            return jsonify(error="Text is too long to export."), 400
        try:
            blob, mime, ext = filetools.make_file(content, fmt)
        except filetools.FileError as exc:
            return jsonify(error=str(exc)), 500
        name = _clean_name(data.get("name"))
        return send_file(io.BytesIO(blob), mimetype=mime, as_attachment=True, download_name="%s.%s" % (name, ext))


def read_docs_text(user_id, doc_ids):
    """Return [(filename, text)] for the user's own files (used by the chat)."""
    out = []
    for doc in Document.query.filter(Document.user_id == user_id, Document.id.in_(doc_ids)).all():
        p = os.path.join(Config.UPLOAD_DIR, str(user_id), doc.stored_name + ".txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                out.append((doc.filename, fh.read()))
    return out
