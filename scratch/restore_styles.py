with open('static/css/style.css', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Restore who-box
old_who_block = """/* user card + logout */
.who-box{
  margin-top:auto;padding:14px;gap:10px;
  display:flex;flex-direction:column;
  background:#fff;border:1.5px solid var(--ink);border-radius:18px;box-shadow:var(--hard-lg);
}
.who-top{order:0;display:flex;align-items:center;justify-content:space-between;gap:6px}
.who-name-wrap{display:inline-flex;align-items:center;gap:6px;min-width:0;flex:1}
.who-name{color:var(--ink);font:700 15px var(--display);letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.who-name:hover{color:var(--strong)}
.btn-nickname-edit{background:none;border:0;padding:3px;cursor:pointer;color:var(--mute);display:inline-flex;align-items:center;justify-content:center;border-radius:4px;transition:color .12s,background .12s}
.btn-nickname-edit:hover{color:var(--ink);background:var(--line)}

.who-plan-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:99px;font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;flex-shrink:0}
.who-plan-badge.free{background:rgba(18,192,122,.12);color:#0c8a5a;border:1px solid rgba(18,192,122,.35)}
.who-plan-badge.plus,.who-plan-badge.pro{background:linear-gradient(135deg,#ff9b58,#ee7f35);color:#fff;border:1px solid rgba(238,127,53,.4);box-shadow:0 2px 6px rgba(238,127,53,.35)}
.who-plan-badge.plus_pro,.who-plan-badge.pro_plus{background:linear-gradient(135deg,#7c4dff,#536dfe);color:#fff;border:1px solid rgba(124,77,255,.4);box-shadow:0 2px 6px rgba(124,77,255,.35)}

.nickname-edit-box{order:0;display:flex;align-items:center;gap:5px;width:100%}
.nickname-edit-box[hidden]{display:none!important}
.nickname-input{flex:1;min-width:0;font-size:13px;font-weight:600;color:var(--ink);background:var(--surface);border:1.5px solid var(--ink);border-radius:8px;padding:5px 8px;outline:none}
.nickname-input:focus{border-color:var(--strong);box-shadow:0 0 0 2px rgba(238,127,53,.2)}
.nickname-btn{width:26px;height:26px;border-radius:6px;border:1.5px solid var(--ink);display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;cursor:pointer;flex-shrink:0}
.nickname-btn.save{background:var(--weak-bg);color:var(--weak)}
.nickname-btn.save:hover{background:var(--weak);color:#fff}
.nickname-btn.cancel{background:var(--line);color:var(--mute)}
.nickname-btn.cancel:hover{background:var(--bad);color:#fff;border-color:var(--bad)}

.who-mail{
  order:1;display:flex;align-items:center;gap:8px;
  padding:7px 10px;border:1.5px dashed var(--line2);border-radius:10px;background:var(--hover);
  color:var(--ink2);font-size:12.5px;word-break:break-all;
}
.who-mail::before{content:"";flex:none;width:8px;height:8px;border-radius:50%;background:var(--weak-dot);box-shadow:0 0 0 3px rgba(18,192,122,.2)}
.who-box form,.who-box form.inline{order:3;display:block;margin:0}"""

idx_who_start = content.find("/* user card + logout */")
idx_who_end = content.find(".who-box > .link{order:3}")

if idx_who_start != -1 and idx_who_end != -1:
    content = content[:idx_who_start] + old_who_block + "\n" + content[idx_who_end:]
    print("Replaced who-box styles successfully.")
else:
    print(f"Indices not found: start={idx_who_start}, end={idx_who_end}")

# 2. Cut off dashboard styles at .btn-ghost:hover
cutoff_marker = ".btn-ghost:hover{background:var(--line);border-color:var(--ink)}"
idx_cutoff = content.find(cutoff_marker)
if idx_cutoff != -1:
    content = content[:idx_cutoff + len(cutoff_marker)] + "\n"
    print("Cut off dashboard styles successfully.")
else:
    print("Cutoff marker not found.")

with open('static/css/style.css', 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated static/css/style.css successfully.")
