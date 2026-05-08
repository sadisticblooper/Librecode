"""
browser.py – PiP browser module for OpenCode AI
================================================
Manages a floating Picture-in-Picture WebView the AI can control.

Tool lifecycle
--------------
  Browser CLOSED  →  AI only sees `spawn_browser`
  Browser OPEN    →  AI also sees navigate, snapshot, click, fill,
                     eval, wait, screenshot, close

Android bridge calls go through MainActivity.instance (Chaquopy).
Flask routes registered via register_routes(app).
"""

import json
import time
import threading

# ── State ─────────────────────────────────────────────────────────────────────

_state = {
    "open":          False,
    "url":           "",
    "chat_id":       None,
    "last_snapshot": None,   # last {url, title, domSummary, screenshot}
}

_events: list = []           # page-side events (load, click, input, scroll)
_events_lock = threading.Lock()

# ── Android bridge ─────────────────────────────────────────────────────────────

def _activity():
    """Return the live MainActivity instance via Chaquopy, or None."""
    try:
        from com.opencode.app import MainActivity   # type: ignore
        return MainActivity.instance
    except Exception:
        return None


def _call(method: str, *args):
    """Invoke a method on MainActivity.  Raises if unavailable."""
    act = _activity()
    if act is None:
        raise RuntimeError("MainActivity not available – browser bridge offline")
    return getattr(act, method)(*args)


# ── spawn_browser (always shown) ───────────────────────────────────────────────

SPAWN_BROWSER_TOOL = {
    "type": "function",
    "function": {
        "name": "spawn_browser",
        "description": (
            "Open a floating browser WebView overlay (picture-in-picture). "
            "Call this whenever you need to visit a URL, interact with a live "
            "web page, fill forms, or retrieve content that web_fetch cannot "
            "handle (e.g. JS-heavy pages, logins). "
            "Once opened, browser interaction tools become available: "
            "browser_navigate, browser_snapshot, browser_click, browser_fill, "
            "browser_eval, browser_wait, browser_screenshot, browser_close."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to open (must include https:// or http://)"
                }
            },
            "required": ["url"]
        }
    }
}

# ── Browser interaction tools (only when open) ─────────────────────────────────

_BROWSER_INTERACTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate the browser: go to a URL, or go back / forward / reload.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["url", "back", "forward", "reload"],
                        "description": "Navigation action. Use 'url' + the url field to visit a page."
                    },
                    "url": {
                        "type": "string",
                        "description": "Destination URL (only for action=url)"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": (
                "Get the current page state: URL, title, a readable DOM summary "
                "(headings, links, buttons, inputs with their CSS selectors), "
                "and a base64 PNG screenshot. "
                "Use the selectors from this snapshot with browser_click / browser_fill."
            ),
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element on the page identified by a CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector, e.g. 'button#submit', 'a[href*=login]', 'input[type=checkbox]'"
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "Type text into an input, textarea, or contenteditable element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the target input / textarea"
                    },
                    "value": {
                        "type": "string",
                        "description": "Text to enter"
                    }
                },
                "required": ["selector", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_eval",
            "description": (
                "Evaluate a JavaScript expression or IIFE in the browser page "
                "and return the result as a string. "
                "Examples: 'document.title', '(()=>{ return document.querySelectorAll(\"a\").length })()'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "JS expression or IIFE to run"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_wait",
            "description": (
                "Poll the page until the given text appears in the visible body text, "
                "up to the specified timeout. Use after actions that trigger navigation "
                "or async loading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to wait for"
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Max wait in milliseconds (default 10000)",
                        "default": 10000
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a screenshot of the current browser page. Returns a data URI (base64 PNG).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the floating browser overlay and release its resources.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
]

# All browser tool names for quick membership checks
TOOL_NAMES: set = {t["function"]["name"] for t in _BROWSER_INTERACTION_TOOLS}
TOOL_NAMES.add("spawn_browser")


# ── Public API ─────────────────────────────────────────────────────────────────

def get_tools() -> list:
    """
    Returns the browser tool(s) to merge into the active tools list.
    When closed → [spawn_browser].
    When open   → [spawn_browser] + all interaction tools.
    """
    if _state["open"]:
        return [SPAWN_BROWSER_TOOL] + _BROWSER_INTERACTION_TOOLS
    return [SPAWN_BROWSER_TOOL]


def handles(name: str) -> bool:
    """True if this module should handle a tool with this name."""
    return name in TOOL_NAMES


# ── JS helpers injected into the browser page ──────────────────────────────────

_DOM_SUMMARY_JS = r"""
(function() {
  var results = [];
  var tags = 'h1,h2,h3,h4,a,button,input,textarea,select,label,p,li,span[role],div[role]';
  var els = Array.from(document.querySelectorAll(tags)).slice(0, 120);
  els.forEach(function(el, idx) {
    var tag  = el.tagName.toLowerCase();
    var text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().slice(0, 100);
    // Build a usable CSS selector
    var sel = tag;
    if (el.id)        sel += '#' + el.id;
    else if (el.name) sel += '[name=' + JSON.stringify(el.name) + ']';
    else if (el.type) sel += '[type=' + el.type + ']';
    var attrs = [];
    if (el.href)  attrs.push('href=' + el.href.slice(0, 60));
    if (el.type)  attrs.push('type=' + el.type);
    if (el.role)  attrs.push('role=' + el.role);
    results.push('[' + idx + '] <' + sel + '>' + (text ? ' ' + text : '') + (attrs.length ? '  {' + attrs.join(', ') + '}' : ''));
  });
  return JSON.stringify({
    url:        location.href,
    title:      document.title,
    domSummary: results.join('\n')
  });
})()
"""

_PAGE_EVENT_JS = r"""
(function() {
  if (window.__aiEventsAttached) return;
  window.__aiEventsAttached = true;
  function post(type, data) {
    fetch('/browser_event', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ type: type, data: data, ts: Date.now() })
    }).catch(function(){});
  }
  window.addEventListener('load', function() {
    post('load', { url: location.href, title: document.title });
  });
  // Debounced scroll
  var scrollTimer;
  window.addEventListener('scroll', function() {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function() {
      post('scroll', { x: window.scrollX, y: window.scrollY });
    }, 300);
  });
})();
"""


# ── Tool implementations ───────────────────────────────────────────────────────

def _tool_spawn_browser(args: dict) -> str:
    url = args.get("url", "https://www.google.com")
    try:
        _call("browserOpen", url)
        _state["open"]    = True
        _state["url"]     = url
        return (
            f"Browser opened → {url}\n"
            "Browser tools now available: browser_navigate, browser_snapshot, "
            "browser_click, browser_fill, browser_eval, browser_wait, "
            "browser_screenshot, browser_close."
        )
    except Exception as exc:
        return f"Error opening browser: {exc}"


def _tool_navigate(args: dict) -> str:
    action = args.get("action", "url")
    url    = args.get("url", "")

    if action == "url":
        if not url:
            return "Error: 'url' field is required when action=url"
        try:
            _call("browserLoadUrl", url)
            _state["url"] = url
            return f"Navigating to {url}"
        except Exception as exc:
            return f"Error: {exc}"

    js_map = {
        "back":    "history.back()",
        "forward": "history.forward()",
        "reload":  "location.reload()",
    }
    js = js_map.get(action)
    if not js:
        return f"Unknown action: {action!r}.  Use url | back | forward | reload"
    return _tool_eval({"code": js})


def _tool_snapshot(args: dict) -> str:
    try:
        raw = _call("browserEvalSync", _DOM_SUMMARY_JS)
        if not raw:
            return "Snapshot returned empty – page may still be loading."
        # Strip outer JSON quotes that evaluateJavascript wraps around strings
        raw_str = str(raw)
        if raw_str.startswith('"') and raw_str.endswith('"'):
            try:
                raw_str = json.loads(raw_str)
            except Exception:
                pass
        data = json.loads(raw_str)
        _state["last_snapshot"] = data

        # Grab screenshot separately (non-blocking)
        screenshot_b64 = ""
        try:
            snap_json = str(_call("browserSnapshotSync"))
            if snap_json:
                snap = json.loads(snap_json)
                screenshot_b64 = snap.get("screenshot", "")
        except Exception:
            pass

        lines = [
            f"URL:   {data.get('url', '?')}",
            f"Title: {data.get('title', '?')}",
            "",
            "DOM summary (use selectors with browser_click / browser_fill):",
            data.get("domSummary", "(empty)"),
        ]
        if screenshot_b64:
            lines.append(f"\n[screenshot captured – {len(screenshot_b64)} chars base64]")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error taking snapshot: {exc}"


def _tool_click(args: dict) -> str:
    selector = args.get("selector", "")
    if not selector:
        return "Error: selector is required"
    js = (
        "(function(){"
        f"  var el = document.querySelector({json.dumps(selector)});"
        "  if (!el) return 'Element not found: " + selector.replace("'", "\\'") + "';"
        "  el.click();"
        "  return 'Clicked';"
        "})()"
    )
    return _tool_eval({"code": js})


def _tool_fill(args: dict) -> str:
    selector = args.get("selector", "")
    value    = args.get("value", "")
    if not selector:
        return "Error: selector is required"
    js = (
        "(function(){"
        f"  var el = document.querySelector({json.dumps(selector)});"
        "  if (!el) return 'Element not found: " + selector.replace("'", "\\'") + "';"
        "  el.focus();"
        f"  el.value = {json.dumps(value)};"
        "  el.dispatchEvent(new Event('input',  {bubbles:true}));"
        "  el.dispatchEvent(new Event('change', {bubbles:true}));"
        "  return 'Filled';"
        "})()"
    )
    return _tool_eval({"code": js})


def _tool_eval(args: dict) -> str:
    code = args.get("code", "")
    if not code:
        return "Error: code is required"
    try:
        result = _call("browserEvalSync", code)
        return str(result) if result is not None else "null"
    except Exception as exc:
        return f"Error evaluating JS: {exc}"


def _tool_wait(args: dict) -> str:
    text       = args.get("text", "")
    timeout_ms = int(args.get("timeout_ms", 10000))
    if not text:
        return "Error: text is required"
    deadline = time.time() + timeout_ms / 1000.0
    check_js = f"document.body && document.body.innerText.includes({json.dumps(text)})"
    while time.time() < deadline:
        try:
            val = str(_call("browserEvalSync", check_js)).lower()
            if val == "true":
                return f"Found: {text!r}"
        except Exception:
            pass
        time.sleep(0.4)
    return f"Timeout after {timeout_ms}ms – {text!r} did not appear on page"


def _tool_screenshot(args: dict) -> str:
    try:
        raw = str(_call("browserSnapshotSync"))
        if not raw:
            return "No screenshot available – page may not be loaded"
        data = json.loads(raw)
        b64  = data.get("screenshot", "")
        if not b64:
            return "Screenshot capture returned empty data"
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        return f"Error capturing screenshot: {exc}"


def _tool_close(args: dict) -> str:
    try:
        _call("browserClose")
    except Exception as exc:
        return f"Error closing browser: {exc}"
    _state["open"]          = False
    _state["url"]           = ""
    _state["last_snapshot"] = None
    with _events_lock:
        _events.clear()
    return "Browser closed."


# ── Dispatch table ─────────────────────────────────────────────────────────────

_DISPATCH = {
    "spawn_browser":      _tool_spawn_browser,
    "browser_navigate":   _tool_navigate,
    "browser_snapshot":   _tool_snapshot,
    "browser_click":      _tool_click,
    "browser_fill":       _tool_fill,
    "browser_eval":       _tool_eval,
    "browser_wait":       _tool_wait,
    "browser_screenshot": _tool_screenshot,
    "browser_close":      _tool_close,
}


def run_tool(name: str, args: dict) -> str:
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"Unknown browser tool: {name!r}"
    return fn(args)


# ── Flask routes ───────────────────────────────────────────────────────────────

def register_routes(flask_app) -> None:
    """Attach browser routes to an existing Flask app."""
    from flask import request, jsonify  # type: ignore

    @flask_app.route("/browser_event", methods=["POST"])
    def _browser_event():
        data = request.get_json(force=True, silent=True) or {}
        with _events_lock:
            _events.append(data)
            if len(_events) > 200:          # cap queue
                del _events[:-200]
        # Reflect URL changes in state
        if data.get("type") == "load":
            _state["url"] = data.get("data", {}).get("url", _state["url"])
        return jsonify({"ok": True})

    @flask_app.route("/browser_events", methods=["GET"])
    def _browser_events_poll():
        """Drain and return all pending page-side events."""
        with _events_lock:
            evts = list(_events)
            _events.clear()
        return jsonify({"events": evts})

    @flask_app.route("/browser_state", methods=["GET"])
    def _browser_state():
        return jsonify({
            "open": _state["open"],
            "url":  _state["url"],
        })
