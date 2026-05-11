import sys
import os

# When running under Chaquopy, __file__ is inside the zip.
# Add the librecode_out dir to sys.path so "from python.config import ..."
# resolves correctly.
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

# Monkey-patch stdlib with gevent co-routines BEFORE importing Flask/app.
# This lets two simultaneous SSE streams yield co-operatively instead of
# fighting over the GIL, which was causing both chats to slow to a crawl.
try:
    from gevent import monkey
    monkey.patch_all()
    _USE_GEVENT = True
except ImportError:
    _USE_GEVENT = False

from python.app import app
from python.config import HOST, PORT

def run():
    print(f"LibreCode - http://localhost:{PORT}")
    if _USE_GEVENT:
        from gevent.pywsgi import WSGIServer
        server = WSGIServer((HOST, PORT), app)
        server.serve_forever()
    else:
        # Fallback: threaded Flask (gevent not installed)
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    run()
