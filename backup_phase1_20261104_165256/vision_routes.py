"""Flask routes for the vision feature. Registered in app.py by install_vision.py."""
import re
import uuid

from flask import Blueprint, jsonify, render_template_string, request, send_from_directory

import vision

vision_bp = Blueprint("vision_bp", __name__)

ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}
MAX_BYTES = 15 * 1024 * 1024


def after_vision_request(kind, files=None):
    """Hook: call your plan-limit counter / activity log / My Files saver here.
    kind is 'classify' or 'extract'; files is the list of saved file names."""
    return None


def _read_image():
    f = request.files.get("image")
    if not f or not f.filename:
        return None, None, ("Please choose an image.", 400)
    mime = f.mimetype if f.mimetype in ALLOWED_MIME else "image/png"
    data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None, None, ("Image is larger than 15 MB.", 413)
    if not data:
        return None, None, ("Empty file.", 400)
    return data, mime, None


@vision_bp.route("/vision")
def vision_page():
    return render_template_string(PAGE, formats=vision.FORMATS)


@vision_bp.route("/vision/classify", methods=["POST"])
def vision_classify():
    data, mime, err = _read_image()
    if err:
        return jsonify({"error": err[0]}), err[1]
    labels = [l for l in re.split(r"[,\n]", request.form.get("labels", "")) if l.strip()]
    question = request.form.get("question", "")
    try:
        result = vision.route(data, mime, labels, question)
    except Exception as e:
        return jsonify({"error": f"Vision failed: {e}"}), 500
    if "error" in result:
        return jsonify(result), 400
    after_vision_request("classify")
    return jsonify(result)


@vision_bp.route("/vision/extract", methods=["POST"])
def vision_extract():
    data, mime, err = _read_image()
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
    after_vision_request("extract", files)
    return jsonify({"data": extracted, "job": job, "files": files, "errors": errors,
                    "urls": {f.rsplit(".", 1)[1]: f"/vision/download/{job}/{f}" for f in files},
                    "warning": "Check numbers against the original image."})


@vision_bp.route("/vision/download/<job>/<name>")
def vision_download(job, name):
    if not re.fullmatch(r"[0-9a-f]{16}", job) or not re.fullmatch(r"extracted\.[a-z]{2,4}", name):
        return "Not found", 404
    return send_from_directory((vision.OUT_DIR / job).resolve(), name, as_attachment=True)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vision</title>
<style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#222}
h1{font-size:1.5rem} section{border:1px solid #ddd;border-radius:10px;padding:1rem;margin:1rem 0}
input[type=text],textarea{width:100%;padding:.5rem;margin:.3rem 0;box-sizing:border-box}
button{padding:.55rem 1.1rem;border:0;border-radius:8px;background:#2b5fd9;color:#fff;cursor:pointer}
button:disabled{opacity:.5} label.f{display:inline-block;margin-right:.8rem}
table{border-collapse:collapse;margin:.5rem 0} td,th{border:1px solid #bbb;padding:3px 8px}
pre{white-space:pre-wrap;background:#f6f6f6;padding:.6rem;border-radius:6px}
.err{color:#b00020} .dl a{display:inline-block;margin:.2rem .6rem .2rem 0}
</style></head><body>
<h1>Vision</h1>
<section>
  <h3>Ask about an image</h3>
  <input type="file" id="img1" accept="image/*">
  <input type="text" id="labels" placeholder="Labels, comma separated (cat, dog, car) - optional">
  <input type="text" id="question" placeholder="Or ask a question - optional">
  <button id="go1">Run</button>
  <div id="out1"></div>
</section>
<section>
  <h3>Save the data in this image as files</h3>
  <input type="file" id="img2" accept="image/*"><br>
  {% for f in formats %}<label class="f"><input type="checkbox" class="fmt" value="{{f}}"
    {% if f in ['xlsx','pdf'] %}checked{% endif %}> {{f|upper}}</label>{% endfor %}<br>
  <label><input type="checkbox" id="auto"> Only if the image looks like a table or document</label><br><br>
  <button id="go2">Extract data and save files</button>
  <div id="out2"></div>
  <p><small>Always check numbers against the original image.</small></p>
</section>
<script>
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function post(url,fd,btn,out){
  btn.disabled=true; out.innerHTML='Working... (first run can be slow)';
  try{const r=await fetch(url,{method:'POST',body:fd}); const j=await r.json();
    if(!r.ok||j.error){out.innerHTML='<p class="err">'+esc(j.error||'Error')+'</p>';return null}
    return j}catch(e){out.innerHTML='<p class="err">'+esc(e)+'</p>'}finally{btn.disabled=false}
}
go1.onclick=async()=>{
  const f=img1.files[0]; if(!f){out1.innerHTML='<p class="err">Choose an image.</p>';return}
  const fd=new FormData(); fd.append('image',f); fd.append('labels',labels.value); fd.append('question',question.value);
  const j=await post('/vision/classify',fd,go1,out1); if(!j)return;
  let h='<p><b>Answer:</b></p><pre>'+esc(j.answer)+'</pre><p>Model: '+esc(j.model)+
    (j.confidence!=null?' &middot; confidence '+j.confidence.toFixed(2):'')+
    ' &middot; path: '+esc((j.path||[]).join(' → '))+'</p>';
  if(j.note)h+='<p>'+esc(j.note)+'</p>'; out1.innerHTML=h;
};
go2.onclick=async()=>{
  const f=img2.files[0]; if(!f){out2.innerHTML='<p class="err">Choose an image.</p>';return}
  const fd=new FormData(); fd.append('image',f);
  document.querySelectorAll('.fmt:checked').forEach(c=>fd.append('formats',c.value));
  if(auto.checked)fd.append('auto','1');
  const j=await post('/vision/extract',fd,go2,out2); if(!j)return;
  if(j.skipped){out2.innerHTML='<p>'+esc(j.message)+'</p>';return}
  let h='';
  if(j.data.text)h+='<pre>'+esc(j.data.text)+'</pre>';
  j.data.tables.forEach(t=>{h+='<b>'+esc(t.name)+'</b><table>'+t.rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>').join('')+'</table>'});
  h+='<div class="dl">'+Object.entries(j.urls).map(([k,u])=>'<a href="'+u+'">Download '+k.toUpperCase()+'</a>').join('')+'</div>';
  if(Object.keys(j.errors).length)h+='<p class="err">Could not make: '+esc(JSON.stringify(j.errors))+'</p>';
  out2.innerHTML=h;
};
</script></body></html>"""
