"""Flask routes for Multimodal Vision, Image Classification, Image Generation, and Document Extraction."""
import io
import os
import re
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, render_template_string, request, send_file, send_from_directory, session

import extractor
import image_gen
import multimodal
import vision
from config import Config

vision_bp = Blueprint("vision_bp", __name__)

ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _get_uid():
    return session.get("uid") or "anon"


def _read_image_request():
    f = request.files.get("image") or request.files.get("file")
    if not f or not f.filename:
        return None, None, ("Please choose an image file.", 400)
    data = f.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return None, None, (f"Image exceeds maximum size limit of {MAX_IMAGE_BYTES // (1024*1024)} MB.", 413)
    if not data:
        return None, None, ("Empty file uploaded.", 400)
    mime = f.mimetype if f.mimetype in ALLOWED_IMAGE_MIME else "image/png"
    return data, mime, None


# -----------------------------------------------------------------------------
# 1. Image Classification & Vision Intelligence APIs
# -----------------------------------------------------------------------------
@vision_bp.route("/api/vision/models", methods=["GET"])
def api_vision_models():
    """Returns available vision models and their status."""
    return jsonify({
        "models": [
            {
                "id": "mobilenet_v3_small",
                "name": "MobileNetV3 Small",
                "type": "Fixed-Class Image Classification",
                "classes": 1000,
                "dataset": "ImageNet-1K",
                "device": Config.VISION_DEVICE,
                "status": "active",
                "offline": True,
            },
            {
                "id": "siglip2_base",
                "name": "SigLIP 2 Base",
                "type": "Zero-Shot Open-Vocabulary Classification",
                "status": "active",
                "supports_custom_categories": True,
            },
            {
                "id": "qwen2.5-vl:3b",
                "name": "Qwen2.5-VL-3B-Instruct / Gemini Multimodal",
                "type": "Multimodal Understanding & Visual Question Answering",
                "status": "active",
                "supports_vqa": True,
                "supports_table_extraction": True,
            },
        ]
    })


@vision_bp.route("/api/vision/classify", methods=["POST"])
def api_vision_classify():
    """
    Image classification endpoint.
    - If custom labels provided: routes to SigLIP 2 Base zero-shot classification.
    - If no custom labels: routes to MobileNetV3 Small (fast ImageNet-1K classification).
    """
    data, mime, err = _read_image_request()
    if err:
        return jsonify({"error": err[0]}), err[1]

    labels = [l.strip() for l in re.split(r"[,\n]", request.form.get("labels", "")) if l.strip()]
    model_choice = request.form.get("model", "auto").lower()

    try:
        if model_choice == "mobilenet" or (model_choice == "auto" and not labels):
            result = vision.classify_mobilenet(data)
        else:
            result = vision.classify_siglip(data, candidate_labels=labels)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"error": f"Image classification failed: {str(e)}"}), 500


@vision_bp.route("/api/vision/understand", methods=["POST"])
def api_vision_understand():
    """
    Visual Question Answering & Image Understanding endpoint.
    Routes to Qwen2.5-VL-3B-Instruct with seamless Gemini Multimodal fallback.
    """
    data, mime, err = _read_image_request()
    if err:
        return jsonify({"error": err[0]}), err[1]

    question = (request.form.get("question") or request.form.get("prompt") or "").strip()
    try:
        result = vision.understand_image(data, mime=mime, question=question)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"error": f"Image understanding failed: {str(e)}"}), 500


# -----------------------------------------------------------------------------
# 2. Image Generation & Editing APIs
# -----------------------------------------------------------------------------
@vision_bp.route("/api/image/models", methods=["GET"])
def api_image_models():
    """Returns available image generation providers and configuration status."""
    return jsonify({
        "providers": image_gen.get_available_providers(),
        "low_cost_model": getattr(Config, "IMAGE_GEN_LOW_MODEL", "dall-e-2"),
        "premium_model": getattr(Config, "IMAGE_GEN_PREMIUM_MODEL", "imagen-3.0-generate-002"),
    })


@vision_bp.route("/api/image/generate", methods=["POST"])
def api_image_generate():
    """
    Text-to-image generation endpoint.
    Analyzes prompt complexity, calls selected low-cost or premium provider,
    persists image, and returns preview and download URLs.
    """
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Please provide an image prompt."}), 400

    requested_model = data.get("model")
    requested_size = data.get("size")
    uid = _get_uid()

    try:
        result = image_gen.generate_image(
            prompt=prompt,
            user_id=uid,
            requested_model=requested_model,
            requested_size=requested_size,
        )
        return jsonify({"success": True, "image": result})
    except image_gen.ImageGenError as exc:
        return jsonify({"error": exc.message, "code": exc.code, "details": exc.details}), exc.status_code
    except Exception as exc:
        return jsonify({"error": f"Image generation failed: {str(exc)}"}), 500


@vision_bp.route("/api/image/edit", methods=["POST"])
def api_image_edit():
    """Image-to-image editing endpoint."""
    img_bytes, mime, err = _read_image_request()
    if err:
        return jsonify({"error": err[0]}), err[1]
    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Please provide editing instructions."}), 400

    uid = _get_uid()
    try:
        result = image_gen.edit_image(img_bytes, prompt, user_id=uid)
        return jsonify({"success": True, "image": result})
    except image_gen.ImageGenError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), exc.status_code
    except Exception as exc:
        return jsonify({"error": f"Image editing failed: {str(exc)}"}), 500


@vision_bp.route("/api/image/download/<token>", methods=["GET"])
def api_image_download(token):
    """Serves generated or edited images safely."""
    if not re.fullmatch(r"[0-9a-fA-F_]{16,64}", token):
        return "Invalid image token", 400

    # Search in user directories
    upload_root = Path(Config.UPLOAD_DIR)
    candidates = list(upload_root.glob(f"*/generated/{token}.png")) + list(upload_root.glob(f"generated/{token}.png"))
    if not candidates:
        return "Image not found", 404

    target = candidates[0].resolve()
    if not str(target).startswith(str(upload_root.resolve())):
        return "Access denied", 403

    as_attachment = request.args.get("download") == "1"
    return send_file(target, mimetype="image/png", as_attachment=as_attachment, download_name=f"upian-{token[:8]}.png")


# -----------------------------------------------------------------------------
# 3. Multimodal Upload System (PNG, JPG, JPEG, WEBP, PDF, TXT, CSV, DOCX, XLSX)
# -----------------------------------------------------------------------------
@vision_bp.route("/api/upload", methods=["POST"])
def api_multimodal_upload():
    """
    Universal drag-and-drop & file selection upload endpoint.
    Supports all 9 required formats: PNG, JPG, JPEG, WEBP, PDF, TXT, CSV, DOCX, XLSX.
    Validates magic bytes, verifies size, generates thumbnail for images, and stores securely.
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Please select a file to upload."}), 400

    raw_bytes = f.read(Config.MAX_CONTENT_LENGTH)
    uid = _get_uid()

    try:
        saved = multimodal.save_uploaded_file(raw_bytes, f.filename, uid)
        return jsonify({"success": True, "file": saved})
    except multimodal.ValidationError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), exc.status_code
    except Exception as exc:
        return jsonify({"error": f"Upload failed: {str(exc)}"}), 500


@vision_bp.route("/api/files/<user_id>/thumb/<token>", methods=["GET"])
def api_file_thumb(user_id, token):
    if not re.fullmatch(r"[0-9a-fA-F]{32}", token):
        return "Invalid token", 400
    user_dir = (Path(Config.UPLOAD_DIR) / str(user_id)).resolve()
    thumb_path = (user_dir / f"{token}_thumb.jpg").resolve()
    if not str(thumb_path).startswith(str(user_dir)) or not thumb_path.exists():
        return "Thumbnail not found", 404
    return send_file(thumb_path, mimetype="image/jpeg")


@vision_bp.route("/api/files/<user_id>/download/<token>", methods=["GET"])
def api_file_download(user_id, token):
    if not re.fullmatch(r"[0-9a-fA-F]{32}", token):
        return "Invalid token", 400
    user_dir = (Path(Config.UPLOAD_DIR) / str(user_id)).resolve()
    candidates = [p for p in user_dir.glob(f"{token}.*") if not p.name.endswith("_thumb.jpg")]
    if not candidates:
        return "File not found", 404
    target = candidates[0].resolve()
    if not str(target).startswith(str(user_dir)):
        return "Access denied", 403
    return send_file(target, as_attachment=request.args.get("download") == "1")


# -----------------------------------------------------------------------------
# 4. Document & Data Extraction APIs
# -----------------------------------------------------------------------------
@vision_bp.route("/api/extract", methods=["POST"])
def api_extract():
    """
    Extracts text and tables from any uploaded file or image.
    Returns structured JSON with preserved table headers and rows.
    """
    f = request.files.get("file") or request.files.get("image")
    if not f or not f.filename:
        return jsonify({"error": "Please provide a document or image to extract."}), 400

    raw_bytes = f.read(Config.MAX_CONTENT_LENGTH)
    try:
        data, ext, kind = extractor.extract_from_file_or_image(raw_bytes, f.filename)
        return jsonify({"success": True, "kind": kind, "extension": ext, "data": data})
    except Exception as exc:
        return jsonify({"error": f"Extraction failed: {str(exc)}"}), 500


@vision_bp.route("/api/extract/export", methods=["POST"])
def api_extract_export():
    """
    Builds downloadable files in all 6 formats: PDF, DOCX, XLSX, CSV, TXT, JSON.
    """
    payload = request.get_json(silent=True) or {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid extracted data format."}), 400

    job_id = uuid.uuid4().hex[:16]
    job_dir = Path(Config.UPLOAD_DIR) / "exports" / job_id
    generated = extractor.export_all_formats(data, job_dir)

    urls = {fmt: f"/api/extract/download/{job_id}/extracted.{fmt}" for fmt in generated}
    return jsonify({"success": True, "job_id": job_id, "formats": list(generated.keys()), "urls": urls})


@vision_bp.route("/api/extract/download/<job>/<name>", methods=["GET"])
def api_extract_download(job, name):
    if not re.fullmatch(r"[0-9a-fA-F]{16}", job) or not re.fullmatch(r"extracted\.[a-z]{2,4}", name):
        return "Not found", 404
    folder = (Path(Config.UPLOAD_DIR) / "exports" / job).resolve()
    target = (folder / name).resolve()
    if not str(target).startswith(str(folder)) or not target.exists():
        return "File not found", 404
    return send_file(target, as_attachment=True)


# -----------------------------------------------------------------------------
# Backwards-compatible routes for /vision and legacy test endpoints
# -----------------------------------------------------------------------------
@vision_bp.route("/vision")
def vision_page():
    return render_template_string(PAGE, formats=vision.FORMATS)


@vision_bp.route("/vision/classify", methods=["POST"])
def vision_classify():
    data, mime, err = _read_image_request()
    if err:
        return jsonify({"error": err[0]}), err[1]
    labels = [l.strip() for l in re.split(r"[,\n]", request.form.get("labels", "")) if l.strip()]
    question = (request.form.get("question") or "").strip()
    try:
        result = vision.route(data, mime, labels, question)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Vision failed: {str(e)}"}), 500


@vision_bp.route("/vision/extract", methods=["POST"])
def vision_extract():
    data, mime, err = _read_image_request()
    if err:
        return jsonify({"error": err[0]}), err[1]
    formats = [f for f in request.form.getlist("formats") if f in vision.FORMATS] or ["xlsx", "pdf"]
    auto = request.form.get("auto") == "1"
    if auto and not vision.looks_like_document(data):
        return jsonify({"skipped": True, "message": "Image does not look like a table or document."})
    try:
        extracted = vision.extract_data(data, mime)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    job = uuid.uuid4().hex[:16]
    files, errors = vision.export_files(extracted, formats, job)
    return jsonify({
        "data": extracted,
        "job": job,
        "files": files,
        "errors": errors,
        "urls": {f.rsplit(".", 1)[1]: f"/vision/download/{job}/{f}" for f in files},
        "warning": "Check numbers against the original image.",
    })


@vision_bp.route("/vision/download/<job>/<name>")
def vision_download(job, name):
    if not re.fullmatch(r"[0-9a-f]{16}", job) or not re.fullmatch(r"extracted\.[a-z]{2,4}", name):
        return "Not found", 404
    return send_from_directory((vision.OUT_DIR / job).resolve(), name, as_attachment=True)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UPIAN Vision & Multimodal Gateway</title>
<style>
body{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#1e293b;background:#f8fafc}
h1{font-size:1.75rem;color:#0f172a} section{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:1.25rem;margin:1.25rem 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}
input[type=text],textarea{width:100%;padding:.6rem;margin:.4rem 0;border:1px solid #cbd5e1;border-radius:6px;box-sizing:border-box}
button{padding:.6rem 1.2rem;border:0;border-radius:8px;background:#2563eb;color:#fff;font-weight:600;cursor:pointer}
button:hover{background:#1d4ed8} button:disabled{opacity:.5} label.f{display:inline-block;margin-right:.8rem;font-size:.9rem}
table{border-collapse:collapse;margin:.6rem 0;width:100%} td,th{border:1px solid #e2e8f0;padding:6px 10px;text-align:left} th{background:#f1f5f9}
pre{white-space:pre-wrap;background:#f1f5f9;padding:.8rem;border-radius:8px;border:1px solid #e2e8f0}
.err{color:#dc2626} .dl a{display:inline-block;margin:.3rem .8rem .3rem 0;padding:.3rem .6rem;background:#e0e7ff;color:#3730a3;border-radius:6px;text-decoration:none;font-size:.85rem;font-weight:600}
</style></head><body>
<h1>UPIAN Vision & Multimodal Gateway</h1>
<section>
  <h3>1. Image Classification & Visual Intelligence</h3>
  <input type="file" id="img1" accept="image/*"><br><br>
  <input type="text" id="labels" placeholder="Custom categories (e.g. cat, dog, car, document) - uses SigLIP 2 Base">
  <input type="text" id="question" placeholder="Or ask a visual question / describe the image - uses Qwen2.5-VL / Gemini">
  <button id="go1">Analyze Image</button>
  <div id="out1"></div>
</section>
<section>
  <h3>2. Structured Data Extraction & File Export</h3>
  <input type="file" id="img2" accept="image/*"><br><br>
  {% for f in formats %}<label class="f"><input type="checkbox" class="fmt" value="{{f}}"
    {% if f in ['xlsx','pdf','docx','csv','txt','json'] %}checked{% endif %}> {{f|upper}}</label>{% endfor %}<br><br>
  <button id="go2">Extract Structured Data & Download Files</button>
  <div id="out2"></div>
</section>
<script>
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function post(url,fd,btn,out){
  btn.disabled=true; out.innerHTML='<p>Processing with AI models...</p>';
  try{const r=await fetch(url,{method:'POST',body:fd}); const j=await r.json();
    if(!r.ok||j.error){out.innerHTML='<p class="err">'+esc(j.error||'Error')+'</p>';return null}
    return j}catch(e){out.innerHTML='<p class="err">'+esc(e)+'</p>'}finally{btn.disabled=false}
}
go1.onclick=async()=>{
  const f=img1.files[0]; if(!f){out1.innerHTML='<p class="err">Choose an image.</p>';return}
  const fd=new FormData(); fd.append('image',f); fd.append('labels',labels.value); fd.append('question',question.value);
  const j=await post('/vision/classify',fd,go1,out1); if(!j)return;
  let h='<p><b>Answer / Top Class:</b></p><pre>'+esc(j.answer)+'</pre><p>Model: '+esc(j.model)+
    (j.confidence!=null?' &middot; Confidence: '+(j.confidence*100).toFixed(1)+'%':'')+
    ' &middot; Engine: '+esc(j.engine||j.model)+'</p>';
  if(j.uncertainty)h+='<p>Uncertainty Rating: <b>'+esc(j.uncertainty.uncertainty)+'</b> (Entropy: '+j.uncertainty.entropy+', Margin: '+j.uncertainty.margin+')</p>';
  if(j.labels&&j.labels.length>1)h+='<p>Ranked: '+j.labels.map(l=>esc(l.label)+' ('+(l.score*100).toFixed(1)+'%)').join(', ')+'</p>';
  out1.innerHTML=h;
};
go2.onclick=async()=>{
  const f=img2.files[0]; if(!f){out2.innerHTML='<p class="err">Choose an image.</p>';return}
  const fd=new FormData(); fd.append('image',f);
  document.querySelectorAll('.fmt:checked').forEach(c=>fd.append('formats',c.value));
  const j=await post('/vision/extract',fd,go2,out2); if(!j)return;
  let h='<h4>'+esc(j.data.title||'Extracted Document')+'</h4>';
  if(j.data.summary)h+='<p><b>Summary:</b> '+esc(j.data.summary)+'</p>';
  if(j.data.text)h+='<pre>'+esc(j.data.text)+'</pre>';
  (j.data.tables||[]).forEach(t=>{h+='<b>'+esc(t.name)+'</b><table>'+t.rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>').join('')+'</table>'});
  h+='<div class="dl">'+Object.entries(j.urls).map(([k,u])=>'<a href="'+u+'">Download '+k.toUpperCase()+'</a>').join('')+'</div>';
  out2.innerHTML=h;
};
</script></body></html>"""
