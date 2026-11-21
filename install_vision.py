"""Adds the vision blueprint to app.py. Safe to run twice. Backup: app.py.bak_vision."""
import re
import shutil
import sys
from pathlib import Path

APP = Path("app.py")
BACKUP = Path("app.py.bak_vision")
MARK = "# --- vision feature ---"
BLOCK = f"""{MARK}
from vision_routes import vision_bp
if "vision_bp" not in app.blueprints:
    app.register_blueprint(vision_bp)
# --- end vision feature ---
"""

if not APP.exists():
    sys.exit("app.py not found. Run this from your project folder.")
src = APP.read_text(encoding="utf-8")
if MARK in src:
    print("Already installed. Nothing to do.")
    sys.exit(0)
if not re.search(r"^\s*app\s*=\s*Flask\(", src, re.M):
    sys.exit("Could not find 'app = Flask(...)' in app.py. Send me app.py and I'll adjust.")

if not BACKUP.exists():
    shutil.copy(APP, BACKUP)

m = re.search(r"^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", src, re.M)
if m:
    src = src[:m.start()] + BLOCK + "\n\n" + src[m.start():]
else:
    src = src.rstrip() + "\n\n" + BLOCK
APP.write_text(src, encoding="utf-8")
print("Done. Backup saved as app.py.bak_vision. Restart the app, then open /vision")
