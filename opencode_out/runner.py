import sys
import os

# When running under Chaquopy, __file__ is inside the zip.
# Add the opencode_out dir to sys.path so "from python.config import ..."
# resolves correctly.
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from python.app import app
from python.config import HOST, PORT

def run():
    print(f"OpenCode - http://localhost:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    run()
