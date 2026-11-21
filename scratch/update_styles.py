import os

css_path = 'static/css/style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = """/* ---------- pages ---------- */
.page-head h1{width:fit-content;padding:0 8px;margin-left:-8px;border-radius:4px;background:linear-gradient(transparent 62%,var(--marker) 62%,var(--marker) 92%,transparent 92%)}"""

replacement = """/* ---------- pages ---------- */
.page-head{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:28px;flex-wrap:wrap}
.page-head-title,.page-head > div:first-child{flex:1;min-width:280px}
.page-head h1{position:relative;display:inline-block;font:800 34px/1.15 var(--display);letter-spacing:-.035em;color:var(--ink);background:none;padding:0;margin:0}
.page-head h1::after{content:"";display:block;width:44px;height:4px;border-radius:99px;background:linear-gradient(90deg,#ff5a4d 0%,#ff9b58 50%,#12c07a 100%);margin-top:8px;transition:width .35s cubic-bezier(.16,1,.3,1)}
.page-head:hover h1::after{width:76px}
.page-head .sub{color:var(--ink2);font-size:14.5px;line-height:1.55;margin-top:10px;max-width:65ch;opacity:.9}

/* Creative Page Badge */
.page-badge{display:inline-flex;align-items:center;gap:7px;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--strong);background:var(--strong-bg);padding:5px 12px;border-radius:99px;margin-bottom:10px;border:1.5px solid var(--ink);box-shadow:2px 2px 0 var(--ink)}
.page-badge .pulse-dot{width:6px;height:6px;border-radius:50%;background:var(--strong);box-shadow:0 0 0 0 rgba(232,129,61,.7);animation:badgePulse 2s infinite}
@keyframes badgePulse{0%{transform:scale(.95);box-shadow:0 0 0 0 rgba(232,129,61,.7)}70%{transform:scale(1);box-shadow:0 0 0 5px rgba(232,129,61,0)}100%{transform:scale(.95);box-shadow:0 0 0 0 rgba(232,129,61,0)}}

/* Storage Quota Card */
.storage-card{background:var(--surface);border:1.5px solid var(--ink);border-radius:14px;padding:14px 18px;box-shadow:var(--hard);min-width:260px;max-width:320px;flex-shrink:0}
.storage-card-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
.storage-label{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--ink2)}
.storage-usage{font-size:13.5px;color:var(--ink)}
.storage-usage b{font-weight:700;color:var(--strong)}
.storage-bar-track{width:100%;height:8px;background:var(--code);border:1px solid var(--line2);border-radius:99px;overflow:hidden;margin-bottom:8px}
.storage-bar-fill{height:100%;background:linear-gradient(90deg,#12c07a,#ff9b58);border-radius:99px;transition:width .4s ease}
.storage-bar-fill.warn{background:#ff9b58}
.storage-bar-fill.danger{background:var(--coral)}
.storage-card-footer{display:flex;justify-content:space-between;align-items:center;font-size:11.5px;color:var(--mute);font-weight:500}

/* Creative Dropzone */
.creative-dropzone{position:relative;border:2px dashed var(--line2);border-radius:18px;background:linear-gradient(180deg,#fff 0%,#faf8f5 100%);padding:32px 24px;text-align:center;margin-bottom:24px;box-shadow:var(--hard);border-color:var(--ink);transition:all .2s cubic-bezier(.2,.8,.2,1);display:flex;flex-direction:column;align-items:center;gap:12px;cursor:pointer}
.creative-dropzone:hover{transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--ink);border-color:var(--strong)}
.creative-dropzone.over{border-color:var(--strong);background:#fff8f3;transform:scale(1.01);box-shadow:6px 6px 0 var(--strong)}
.drop-icon-box{width:56px;height:56px;border-radius:16px;background:var(--strong-bg);color:var(--strong);border:1.5px solid var(--ink);box-shadow:2px 2px 0 var(--ink);display:flex;align-items:center;justify-content:center;transition:transform .2s ease}
.creative-dropzone:hover .drop-icon-box{transform:translateY(-3px)}
.drop-content{display:flex;flex-direction:column;align-items:center;gap:8px}
.drop-prompt{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;font-size:16px}
.drop-main-text{font-weight:700;color:var(--ink)}
.drop-divider{color:var(--mute);font-size:14px}
.btn-browse{font-size:13px;font-weight:700;padding:6px 16px;background:var(--ink);color:#fff;border-radius:10px;border:1.5px solid var(--ink);cursor:pointer;transition:all .15s ease}
.btn-browse:hover{background:var(--strong);border-color:var(--strong)}
.drop-note{color:var(--mute);font-size:12.5px;margin:0}
.format-pills{display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap;margin-top:2px}
.fmt-pill{font-size:11px;font-weight:700;padding:3px 8px;border-radius:6px;letter-spacing:.03em;border:1px solid var(--line2);background:var(--surface);color:var(--ink2)}
.fmt-pill.pdf{background:#ffebee;color:#c62828;border-color:#ffcdd2}
.fmt-pill.doc{background:#e8f0fe;color:#1a73e8;border-color:#cce5ff}
.fmt-pill.sheet{background:#e6f4ea;color:#137333;border-color:#ceead6}
.fmt-pill.slide{background:#fef7e0;color:#b06000;border-color:#feefc3}
.fmt-pill.code{background:#f3e8fd;color:#7b1fa2;border-color:#e9d5ff}

/* Creative Documents Table */
.creative-table-wrap{background:var(--surface);border:1.5px solid var(--ink);border-radius:16px;box-shadow:var(--hard);overflow:hidden;margin-bottom:24px}
.files-table th{background:#fbf9f5;padding:12px 16px;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink2);border-bottom:1.5px solid var(--line2)}
.files-table td{padding:13px 16px;vertical-align:middle;border-bottom:1px solid var(--line)}
.files-table tr:last-child td{border-bottom:0}
.file-cell{display:flex;align-items:center;gap:10px}
.file-name{font-weight:600;color:var(--ink);word-break:break-all}
.file-tag{display:inline-flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:800;padding:3px 6px;border-radius:5px;min-width:38px;text-align:center;border:1px solid transparent}
.badge-pdf{background:#ffebee;color:#c62828;border-color:#ffcdd2}
.badge-doc{background:#e8f0fe;color:#1a73e8;border-color:#cce5ff}
.badge-sheet{background:#e6f4ea;color:#137333;border-color:#ceead6}
.badge-slide{background:#fef7e0;color:#b06000;border-color:#feefc3}
.badge-code{background:#f3e8fd;color:#7b1fa2;border-color:#e9d5ff}
.badge-txt{background:#f1ede2;color:#4a4766;border-color:#d9d3c2}
.font-num{font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:12.5px}
.date-cell{font-size:12.5px}
.btn-action{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:5px 10px}

/* Empty State */
.empty-docs-cell{padding:48px 20px!important;text-align:center}
.empty-docs-state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px}
.empty-docs-icon{font-size:42px;line-height:1;margin-bottom:6px}
.empty-docs-title{font-family:var(--display);font-weight:700;font-size:18px;color:var(--ink)}
.empty-docs-sub{color:var(--mute);font-size:13.5px;max-width:44ch;margin:0}"""

if target in content:
    content = content.replace(target, replacement)
    with open(css_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: Replaced target in style.css")
else:
    print("ERROR: Target string not found in style.css")
