import json
import threading

_browser_lock = threading.Lock()
_browser_active = False


def _svc():
    from com.librecode.app import BrowserService
    return BrowserService.getInstance()


def _activity():
    from com.librecode.app import MainActivity
    return MainActivity.instance


def _check_open():
    """Returns an error string if the browser is not currently open, else None."""
    if not _svc().isOpen():
        return (
            "Error: The browser is not open. "
            "Call spawn_browser first to open a floating browser window."
        )
    return None


def _inject_tools():
    global _browser_active
    import python.tools as tools_mod
    with _browser_lock:
        if _browser_active:
            return
        existing = {t["function"]["name"] for t in tools_mod.TOOLS}
        for spec in BROWSER_CONTROL_SPECS + [BROWSER_OPEN_FILE_SPEC] + BROWSER_DEVTOOLS_SPECS:
            if spec["function"]["name"] not in existing:
                tools_mod.TOOLS.append(spec)
        _browser_active = True


def _eject_tools():
    global _browser_active
    import python.tools as tools_mod
    with _browser_lock:
        if not _browser_active:
            return
        control_names = {s["function"]["name"] for s in BROWSER_CONTROL_SPECS + [BROWSER_OPEN_FILE_SPEC] + BROWSER_DEVTOOLS_SPECS}
        tools_mod.TOOLS[:] = [t for t in tools_mod.TOOLS if t["function"]["name"] not in control_names]
        _browser_active = False


def _parse_snapshot_result(result: str, fallback_url: str = "") -> str:
    """
    Parse a snapshot/navigate JSON result and return a human-readable string.
    Handles the paginated format: {url, title, tree, total, offset, limit, remaining}.
    """
    if not result:
        return "Error: Got an empty response — the browser may have crashed or the page timed out."
    try:
        data = json.loads(result)
        if "error" in data:
            return f"Error: {data['error']}"
        url = data.get("url", fallback_url)
        title = data.get("title", "")

        # New paginated format
        tree = data.get("tree")
        total = data.get("total", 0)
        offset = data.get("offset", 0)
        remaining = data.get("remaining", 0)
        shown = total - offset - remaining  # interactive elements in this page

        if tree:
            snap_str = json.dumps(tree, indent=2)
        else:
            snap_str = "(empty snapshot — page may still be loading or has no visible body)"

        if total > 0:
            header = f"URL: {url}\nTitle: {title}\n(Interactive elements {offset + 1}–{offset + shown} of {total} shown)\n\nDOM snapshot:\n{snap_str}"
        else:
            header = f"URL: {url}\nTitle: {title}\n\nDOM snapshot:\n{snap_str}"

        if remaining > 0:
            next_offset = offset + shown
            header += f"\n\n⚠️ {remaining} more interactive element(s) not shown. Call browser_snapshot with offset={next_offset} to see the next page."

        return header
    except Exception:
        # Not JSON — return raw so the AI can still read it
        return result


def tool_browser_open(url: str = "about:blank", on_load: str = "") -> str:
    svc = _svc()
    if not svc.hasOverlayPermission():
        activity = _activity()
        from android.content import Intent
        from android.provider import Settings
        from android.net import Uri
        intent = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:com.librecode.app"))
        activity.startActivity(intent)
        return "Overlay permission required. Please grant 'Display over other apps' in the settings screen that just opened, then try again."

    result = str(svc.open(url))
    _inject_tools()

    try:
        data = json.loads(result)
        if "error" in data:
            return f"Error opening browser: {data['error']}"
        current_url = data.get("url", url)
        title = data.get("title", "")
        tree = data.get("tree")
        total = data.get("total", 0)
        remaining = data.get("remaining", 0)
        shown = total - remaining
        snap_str = json.dumps(tree, indent=2) if tree else "(no snapshot)"
        page_info = f"(Interactive elements 1–{shown} of {total} shown)" if total > 0 else ""
        remaining_note = f"\n\n⚠️ {remaining} more interactive element(s) not shown. Call browser_snapshot with offset={shown} to see the next page." if remaining > 0 else ""
        out = (
            f"Browser opened: {current_url}\n"
            f"Title: {title}\n"
            f"{page_info}\n\n"
            f"DOM snapshot:\n{snap_str}\n"
            f"{remaining_note}\n\n"
            f"Browser control tools are now available:\n"
            f"  browser_snapshot   - get interactive elements with UIDs (paginate with offset=; use browser_html/browser_dom_query if element not found)\n"
            f"  browser_click      - click element by UID\n"
            f"  browser_fill       - type into input by UID\n"
            f"  browser_navigate   - go to a URL (returns new snapshot)\n"
            f"  browser_eval       - run JavaScript — MUST end with `return <value>` or be a single expression\n"
            f"  browser_html       - get full page outerHTML (use when snapshot misses elements)\n"
            f"  browser_dom_query  - find elements by CSS selector (e.g. 'input[type=password]')\n"
            f"  browser_network    - get all XHR/fetch calls captured since page load\n"
            f"  browser_wait       - wait for text to appear\n"
            f"  browser_screenshot - capture screenshot as base64\n"
            f"  browser_cookies    - get cookies for a domain\n"
            f"  browser_login_cct  - REQUIRED for Google/GitHub/OAuth login (WebView is always blocked)\n"
            f"  browser_close      - close the browser\n\n"
            f"Use UIDs from the snapshot to interact with elements."
        )
        if on_load.strip():
            js_result = tool_browser_eval(on_load)
            out += f"\n\non_load result:\n{js_result}"
        return out
    except Exception:
        base = f"Browser opened.\n\nRaw result:\n{result}"
        if on_load.strip():
            js_result = tool_browser_eval(on_load)
            base += f"\n\non_load result:\n{js_result}"
        return base


def tool_browser_snapshot(offset: int = 0) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().snapshot(offset))
    return _parse_snapshot_result(result)


def tool_browser_click(uid: str) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().click(uid))
    if result.startswith("error"):
        return result
    import time
    time.sleep(0.8)
    return f"{result}\n\n" + tool_browser_snapshot()


def tool_browser_fill(uid: str, value: str) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().fill(uid, value))
    if result.startswith("error"):
        return result
    # Return updated snapshot so AI doesn't need a separate browser_snapshot call
    return f"{result}\n\n" + tool_browser_snapshot()


def tool_browser_navigate(url: str) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().navigate(url))
    parsed = _parse_snapshot_result(result, fallback_url=url)
    # Prepend "Navigated to:" context when it succeeded
    if not parsed.startswith("Error"):
        parsed = f"Navigated to: {url}\n" + parsed
    return parsed


def tool_browser_eval(script: str) -> str:
    err = _check_open()
    if err:
        return err
    raw = str(_svc().evaluate(script))
    if not raw:
        return "Error: JavaScript evaluation returned an empty result."
    # evaluate() now returns JSON.stringify'd output so we can distinguish
    # undefined (→ null) from the string "undefined".
    try:
        value = json.loads(raw)
        if value is None:
            return "(no return value — script completed but returned undefined)"
        # If the value is already a string, return it directly; otherwise pretty-print.
        return value if isinstance(value, str) else json.dumps(value, indent=2)
    except Exception:
        # Fallback: return raw as-is (e.g. if old Java path fires)
        return raw


def tool_browser_wait(text: str, timeout_ms: int = 15000) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().waitForText(text, timeout_ms))
    try:
        data = json.loads(result)
        if "error" in data:
            return data["error"]
        # waitForText returns a fresh snapshot — use tree key (new format)
        tree = data.get("tree") or data.get("snapshot")  # fallback for compat
        snap_str = json.dumps(tree, indent=2)[:8000] if tree else ""
        total = data.get("total", 0)
        remaining = data.get("remaining", 0)
        shown = total - remaining
        page_note = f"(elements 1–{shown} of {total})" if total > 0 else ""
        remaining_note = f"\n⚠️ {remaining} more element(s) — call browser_snapshot with offset={shown} to continue." if remaining > 0 else ""
        return f"Found '{text}' {page_note}\n\nDOM snapshot:\n{snap_str}{remaining_note}"
    except Exception:
        return result


def tool_browser_screenshot() -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().screenshot())
    if not result or result.startswith("error"):
        return result if result else "Error: Screenshot failed — no data returned."
    return result


def tool_browser_cookies(url: str) -> str:
    err = _check_open()
    if err:
        return err
    cookies = str(_svc().getCookies(url))
    return cookies if cookies else f"No cookies found for {url}"


def tool_browser_login_cct(url: str) -> str:
    _svc().openCCT(url)
    return (
        f"Chrome tab opened for: {url}\n\n"
        f"The user needs to complete login manually in the browser panel that appeared.\n"
        f"When they're done and the tab closes, call browser_open or browser_navigate to continue — "
        f"session cookies will be carried over automatically."
    )


def tool_browser_close() -> str:
    result = str(_svc().close())
    _eject_tools()
    return result


BROWSER_OPEN_SPEC = {
    "type": "function",
    "function": {
        "name": "spawn_browser",
        "description": (
            "Open a floating browser window and navigate to a URL. "
            "ONLY use this to open or restart the browser — do NOT call this to click things or interact with an already-open page. "
            "If the browser is already open, use browser_click, browser_fill, browser_navigate instead. "
            "Returns a DOM snapshot with UIDs you can use to click/fill elements. "
            "WARNING: Google login never works in this browser — use browser_login_cct instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open (e.g. 'https://google.com')"},
                "on_load": {"type": "string", "description": "Optional JavaScript to run immediately after page load. Result is returned with the snapshot. E.g. 'document.title' or a multi-line script."},
            },
            "required": ["url"],
        },
    },
}

BROWSER_CONTROL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": (
                "Get the current DOM tree of the open browser with UIDs for all interactive elements. "
                "Returns up to 50 interactive elements per call. "
                "If the page has more, the response ends with a warning — call again with offset=50, offset=100 etc to page through. "
                "IMPORTANT: snapshot only shows INTERACTIVE elements (buttons, inputs, links) — NOT all text or page structure. "
                "If you can't find an element: (1) page through with higher offsets, (2) use browser_dom_query with a CSS selector "
                "like 'input[type=password]' or '[name=email]', (3) use browser_html for full HTML, "
                "(4) use browser_screenshot to visually see the page. Never give up after one snapshot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "description": "Index of the first interactive element to show (default 0). Use the value from the '⚠️ N more elements' message to page forward.", "default": 0},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element in the open browser by its UID from a snapshot. THIS is the correct tool to click anything — never call spawn_browser to click. Use the UID from the most recent snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Element UID from snapshot (e.g. '1_5')"},
                },
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "Type a value into an input or textarea by its UID from a snapshot. Returns an updated DOM snapshot — do NOT call browser_snapshot separately after filling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid":   {"type": "string", "description": "Element UID from snapshot"},
                    "value": {"type": "string", "description": "Text to enter"},
                },
                "required": ["uid", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate the open browser to a URL. Returns a new DOM snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_eval",
            "description": (
                "Execute JavaScript in the open browser and return the result. "
                "CRITICAL: Your script runs inside (function(){ YOUR_SCRIPT })() — "
                "it MUST end with `return <value>` or be a single expression. "
                "Without return, you ALWAYS get '(no return value — script completed but returned undefined)'. "
                "CORRECT examples: `return document.title` | `return document.querySelector('input').value` | "
                "`var el = document.querySelector('input[type=email]'); return el ? el.value : 'not found'`. "
                "WRONG (no return = silent undefined): `let x = document.title` | `console.log(x)` | "
                "`document.querySelector('input')` as a statement. "
                "For multi-line scripts, always end with return. "
                "For async: `return fetch('/api').then(r => r.json())`. "
                "Do NOT use for Python — use python_exec for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript ending with explicit `return`, e.g. `return document.title` or single expression `document.title`"},
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_wait",
            "description": "Wait until specific text appears on the page, then return the DOM snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text":       {"type": "string",  "description": "Text to wait for"},
                    "timeout_ms": {"type": "integer", "description": "Max wait in milliseconds (default 15000)", "default": 15000},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a screenshot of the current browser view. Returns a base64-encoded JPEG data URI.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_cookies",
            "description": "Get all cookies for a given URL/domain from the browser session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL or domain to get cookies for (e.g. 'https://google.com')"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_login_cct",
            "description": (
                "Open a real Chrome tab for the user to log in manually (Google, GitHub, OAuth, etc). "
                "USE THIS IMMEDIATELY for any Google login — Google ALWAYS blocks WebView with bot detection. "
                "Signs of bot detection: CAPTCHA, 'couldn't sign you in', challenge screen, "
                "or DOM with no email/password fields when you expect them. "
                "The user logs in manually in the Chrome tab; session cookies carry over automatically. "
                "After the user says they're done, call browser_navigate to continue. "
                "Do NOT keep retrying Google login in WebView — it will never work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Login URL (e.g. 'https://accounts.google.com')"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the floating browser window and end the browser session.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def tool_browser_open_file(path: str) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().openFile(path))
    return _parse_open_result(result)


BROWSER_OPEN_FILE_SPEC = {
    "type": "function",
    "function": {
        "name": "browser_open_file",
        "description": (
            "Load a local HTML file into the open browser by absolute path. "
            "JS, CSS, images and other files the HTML imports are resolved relative "
            "to the file's directory automatically. "
            "The browser must already be open (call spawn_browser first). "
            "Returns a DOM snapshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the HTML file, e.g. '/sdcard/librecode/myapp/index.html'",
                },
            },
            "required": ["path"],
        },
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# DevTools-style tools — all implemented via browser_eval (no new Java needed)
# ─────────────────────────────────────────────────────────────────────────────

# JS snippet injected once per page to intercept network + console.
_DEVTOOLS_INJECT_JS = """
(function() {
  if (window.__ocdvt) return 'already_injected';
  window.__ocdvt = { net: [], con: [] };

  // --- Console capture ---
  ['log','warn','error','info','debug'].forEach(function(m) {
    var orig = console[m];
    console[m] = function() {
      var args = Array.prototype.slice.call(arguments).map(function(a) {
        try { return typeof a === 'object' ? JSON.stringify(a) : String(a); } catch(e) { return String(a); }
      });
      window.__ocdvt.con.push({level: m, msg: args.join(' '), t: Date.now()});
      orig.apply(console, arguments);
    };
  });

  // --- XHR capture (with request + response headers) ---
  var OrigXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function() {
    var xhr = new OrigXHR();
    var entry = {type:'xhr', method:'', url:'', status:null, reqHeaders:{}, req:null, res:null, resHeaders:{}, t:null};
    var origOpen = xhr.open.bind(xhr);
    xhr.open = function(m, u) { entry.method = m; entry.url = u; origOpen(m, u); };
    var origSetHeader = xhr.setRequestHeader.bind(xhr);
    xhr.setRequestHeader = function(k, v) { entry.reqHeaders[k] = v; origSetHeader(k, v); };
    var origSend = xhr.send.bind(xhr);
    xhr.send = function(body) {
      try { entry.req = body ? String(body).slice(0,2000) : null; } catch(e){}
      entry.t = Date.now();
      xhr.addEventListener('loadend', function() {
        entry.status = xhr.status;
        try {
          var rh = {};
          xhr.getAllResponseHeaders().trim().split('\r\n').forEach(function(l) {
            var i = l.indexOf(':'); if (i > 0) rh[l.slice(0,i).trim()] = l.slice(i+1).trim();
          });
          entry.resHeaders = rh;
        } catch(e) {}
        try { entry.res = xhr.responseText ? xhr.responseText.slice(0,4000) : null; } catch(e){}
        window.__ocdvt.net.push(JSON.parse(JSON.stringify(entry)));
      });
      origSend(body);
    };
    return xhr;
  };

  // --- fetch capture (with request + response headers) ---
  var origFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    var url = String(input && input.url || input);
    var entry = {type:'fetch', method:(init&&init.method)||'GET', url:url, status:null, reqHeaders:(init&&init.headers)||{}, req:null, res:null, resHeaders:{}, t:Date.now()};
    try { entry.req = init && init.body ? String(init.body).slice(0,2000) : null; } catch(e){}
    return origFetch(input, init).then(function(resp) {
      entry.status = resp.status;
      try { var rh = {}; resp.headers.forEach(function(v, k) { rh[k] = v; }); entry.resHeaders = rh; } catch(e) {}
      var clone = resp.clone();
      clone.text().then(function(txt) {
        entry.res = txt ? txt.slice(0,4000) : null;
        window.__ocdvt.net.push(JSON.parse(JSON.stringify(entry)));
      });
      return resp;
    });
  };

  return 'injected';
})()
"""


def _ensure_devtools_injected() -> str:
    """Inject the network/console interceptor if not already on this page."""
    # Must prefix with `return` so evaluate()'s wrapper captures the IIFE's value.
    return tool_browser_eval("return " + _DEVTOOLS_INJECT_JS.strip())


def tool_browser_html() -> str:
    """Get the full outer HTML of the current page."""
    err = _check_open()
    if err:
        return err
    return tool_browser_eval("return document.documentElement.outerHTML")


def tool_browser_console() -> str:
    """Get captured console logs (log/warn/error/info) since last inject."""
    err = _check_open()
    if err:
        return err
    _ensure_devtools_injected()
    raw = tool_browser_eval("return JSON.stringify(window.__ocdvt ? window.__ocdvt.con : [])")
    try:
        logs = json.loads(raw) if isinstance(raw, str) else raw
        if not logs:
            return "No console output captured yet."
        lines = [f"[{e.get('level','?').upper()}] {e.get('msg','')}" for e in logs]
        return "\n".join(lines)
    except Exception:
        return raw


def tool_browser_network() -> str:
    """Get captured XHR/fetch requests and responses since last inject."""
    err = _check_open()
    if err:
        return err
    _ensure_devtools_injected()
    raw = tool_browser_eval("return JSON.stringify(window.__ocdvt ? window.__ocdvt.net : [])")
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
        if not entries:
            return "No network requests captured yet. Network capture starts automatically on page load — try reloading or navigating to the page first."
        return json.dumps(entries, indent=2)
    except Exception:
        return raw


def tool_browser_network_start() -> str:
    """Inject the network/console interceptor on the current page so future XHR/fetch calls are captured."""
    err = _check_open()
    if err:
        return err
    result = _ensure_devtools_injected()
    return f"Network + console interception active ({result}). Now trigger the requests you want to capture, then call browser_network."


def tool_browser_local_storage(domain: str = "") -> str:
    """Get all localStorage keys and values for the current page."""
    err = _check_open()
    if err:
        return err
    raw = tool_browser_eval(
        "return JSON.stringify(Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])))"
    )
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not data:
            return "localStorage is empty."
        return json.dumps(data, indent=2)
    except Exception:
        return raw


def tool_browser_session_storage() -> str:
    """Get all sessionStorage keys and values for the current page."""
    err = _check_open()
    if err:
        return err
    raw = tool_browser_eval(
        "return JSON.stringify(Object.fromEntries(Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)])))"
    )
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not data:
            return "sessionStorage is empty."
        return json.dumps(data, indent=2)
    except Exception:
        return raw


def tool_browser_dom_query(selector: str) -> str:
    """Run document.querySelectorAll and return matching elements' outerHTML and text."""
    err = _check_open()
    if err:
        return err
    script = (
        "return JSON.stringify(Array.from(document.querySelectorAll(" + json.dumps(selector) + "))"
        ".map(function(el){return {tag:el.tagName,id:el.id,classes:el.className,"
        "text:el.innerText&&el.innerText.slice(0,500),html:el.outerHTML&&el.outerHTML.slice(0,1000)}}))"
    )
    raw = tool_browser_eval(script)
    try:
        results = json.loads(raw) if isinstance(raw, str) else raw
        if not results:
            return f"No elements matched selector: {selector}"
        return json.dumps(results, indent=2)
    except Exception:
        return raw


def tool_browser_set_cookie(name: str, value: str, domain: str = "", path: str = "/") -> str:
    """Set a cookie on the current page via JavaScript."""
    err = _check_open()
    if err:
        return err
    cookie_str = f"{name}={value}; path={path}"
    if domain:
        cookie_str += f"; domain={domain}"
    script = f"document.cookie = {json.dumps(cookie_str)}; return 'ok'"
    return tool_browser_eval(script)


def tool_browser_clear_network() -> str:
    """Clear the captured network log."""
    err = _check_open()
    if err:
        return err
    return tool_browser_eval("if(window.__ocdvt){window.__ocdvt.net=[];window.__ocdvt.con=[];} return 'cleared'")


BROWSER_DEVTOOLS_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "browser_html",
            "description": "Get the full outer HTML source of the current page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_console",
            "description": "Get captured console.log/warn/error/info output from the current page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_network_start",
            "description": "Re-inject network interceptor on the current page to reset/clear captures (e.g. after a dynamic in-page navigation). Normally not needed — capture starts automatically on every page load. Use this only if you navigated via JS (pushState) without a full reload.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_network",
            "description": "Get all captured XHR and fetch requests/responses since page load. Network capture starts automatically on every page/navigate load — no need to call browser_network_start first. Returns URL, method, status, request headers, request body, response headers, and response body for each request.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_network_clear",
            "description": "Clear the captured network and console log.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_local_storage",
            "description": "Get all localStorage entries for the current page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_session_storage",
            "description": "Get all sessionStorage entries for the current page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_dom_query",
            "description": "Run a CSS selector (querySelectorAll) and return matching elements with their tag, id, classes, text and outerHTML.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector, e.g. 'input[type=email]' or '.nav-link'"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_set_cookie",
            "description": "Set a cookie on the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string", "description": "Cookie name"},
                    "value":  {"type": "string", "description": "Cookie value"},
                    "domain": {"type": "string", "description": "Cookie domain (optional)"},
                    "path":   {"type": "string", "description": "Cookie path (default '/')"},
                },
                "required": ["name", "value"],
            },
        },
    },
]
