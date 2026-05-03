"""
app.py — Flask application entry point.

Imports are kept intentionally thin. All business logic lives in:
  python/agents.py     – agent/prompt loading
  python/compaction/   – context compaction
  python/routes.py     – every Flask route
  python/state.py      – mutable globals
  python/storage.py    – path / file helpers
  python/subagent.py   – subagent runners
  python/tools.py      – tool implementations + TOOLS list
"""

import os
from flask import Flask
from python.config import HOST, PORT
from python.routes import init_app

# ── Paths ──────────────────────────────────────────────────────────────────────
# opencode_out/ sits one level above this file (python/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder=ROOT, static_folder=os.path.join(ROOT, "ui"))

# Register all routes defined in routes.py
init_app(app, ROOT)

# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"OpenCode -- http://localhost:{PORT}")
    app.run(host=HOST, port=PORT, debug=True, threaded=True)
