import json
import threading

_browser_lock = threading.Lock()
_browser_active = False


def _svc():
    from com.opencode.app import BrowserService
    return BrowserService.getInstance()


def _activity():
    from com.opencode.app import MainActivity
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
        for spec in BROWSER_CONTROL_SPECS:
            if spec["function"]["name"] not in existing:
                tools_mod.TOOLS.append(spec)
        _browser_active = True


def _eject_tools():
    global _browser_active
    import python.tools as tools_mod
    with _browser_lock:
        if not _browser_active:
            return
        control_names = {s["function"]["name"] for s in BROWSER_CONTROL_SPECS}
        tools_mod.TOOLS[:] = [t for t in tools_mod.TOOLS if t["function"]["name"] not in control_names]
        _browser_active = False


def _parse_snapshot_result(result: str, fallback_url: str = "") -> str:
    """
    Parse a snapshot/navigate JSON result and return a human-readable string.
    Surfaces any error key clearly instead of silently swallowing it.
    The full snapshot object is passed through without truncation so the AI
    receives the complete DOM tree.
    """
    if not result:
        return "Error: Got an empty response — the browser may have crashed or the page timed out."
    try:
        data = json.loads(result)
        if "error" in data:
            return f"Error: {data['error']}"
        url = data.get("url", fallback_url)
        title = data.get("title", "")
        snapshot = data.get("snapshot")
        if snapshot:
            # Serialize the full snapshot — no truncation
            snap_str = json.dumps(snapshot, indent=2)
        else:
            snap_str = "(empty snapshot — page may still be loading or has no visible body)"
        return f"URL: {url}\nTitle: {title}\n\nDOM snapshot:\n{snap_str}"
    except Exception:
        # Not JSON — return raw so the AI can still read it
        return result


def tool_browser_open(url: str = "about:blank") -> str:
    svc = _svc()
    if not svc.hasOverlayPermission():
        activity = _activity()
        from android.content import Intent
        from android.provider import Settings
        from android.net import Uri
        intent = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:com.opencode.app"))
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
        snapshot = data.get("snapshot")
        snap_str = json.dumps(snapshot, indent=2) if snapshot else "(no snapshot)"
        return (
            f"Browser opened: {current_url}\n"
            f"Title: {title}\n\n"
            f"DOM snapshot:\n{snap_str}\n\n"
            f"Browser control tools are now available:\n"
            f"  browser_snapshot   - get current DOM tree with UIDs\n"
            f"  browser_click      - click element by UID\n"
            f"  browser_fill       - type into input by UID\n"
            f"  browser_navigate   - go to a URL (returns new snapshot)\n"
            f"  browser_eval       - run JavaScript\n"
            f"  browser_wait       - wait for text to appear\n"
            f"  browser_screenshot - capture screenshot as base64\n"
            f"  browser_cookies    - get cookies for a domain\n"
            f"  browser_login_cct  - open Chrome tab for login (Google, GitHub, etc)\n"
            f"  browser_close      - close the browser\n\n"
            f"Use UIDs from the snapshot to interact with elements."
        )
    except Exception:
        return f"Browser opened.\n\nRaw result:\n{result}"


def tool_browser_snapshot() -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().snapshot())
    return _parse_snapshot_result(result)


def tool_browser_click(uid: str) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().click(uid))
    if result.startswith("error"):
        return result
    import time
    time.sleep(0.5)
    # Does NOT auto-snapshot — use browser_snapshot explicitly when needed,
    # or batch multiple actions with browser_batch.
    return result


def tool_browser_fill(uid: str, value: str) -> str:
    err = _check_open()
    if err:
        return err
    result = str(_svc().fill(uid, value))
    if result.startswith("error"):
        return result
    return result


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
        snapshot = data.get("snapshot")
        snap_str = json.dumps(snapshot, indent=2)[:8000] if snapshot else ""
        return f"Found '{text}'\n\nDOM snapshot:\n{snap_str}"
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


def tool_browser_batch(actions: list) -> str:
    """
    Run multiple browser actions in sequence in a single tool call.
    Each action is a dict: {"action": "click"|"fill"|"eval"|"snapshot", ...params}
    Returns a list of results joined by newlines. Use this to avoid repeated
    round-trips for multi-step form fills, multi-click sequences, etc.
    """
    err = _check_open()
    if err:
        return err
    results = []
    import time
    for step in actions:
        act = step.get("action", "")
        if act == "click":
            r = str(_svc().click(step.get("uid", "")))
            results.append(f"click({step.get('uid','')}): {r}")
            time.sleep(0.4)
        elif act == "fill":
            r = str(_svc().fill(step.get("uid", ""), step.get("value", "")))
            results.append(f"fill({step.get('uid','')}): {r}")
        elif act == "eval":
            r = str(_svc().evaluate(step.get("script", "")))
            if r:
                try:
                    v = json.loads(r)
                    r = v if isinstance(v, str) else json.dumps(v, indent=2)
                except Exception:
                    pass
            results.append(f"eval: {r}")
        elif act == "snapshot":
            results.append(tool_browser_snapshot())
        elif act == "navigate":
            results.append(tool_browser_navigate(step.get("url", "")))
        elif act == "wait":
            results.append(tool_browser_wait(step.get("text", ""), step.get("timeout_ms", 10000)))
        else:
            results.append(f"unknown action: {act}")
    return "\n".join(results)


def tool_browser_rg(pattern: str, case_insensitive: bool = False) -> str:
    """Search the current page text using a regex pattern (like rg but on page content)."""
    err = _check_open()
    if err:
        return err
    import re as _re
    # Get full page text via JS
    raw = str(_svc().evaluate("document.body ? document.body.innerText : ''"))
    try:
        page_text = json.loads(raw) if raw else ""
    except Exception:
        page_text = raw
    if not page_text:
        return "No page text available."
    flags = _re.IGNORECASE if case_insensitive else 0
    try:
        matches = []
        for i, line in enumerate(page_text.splitlines(), 1):
            if _re.search(pattern, line, flags):
                matches.append(f"{i}: {line}")
        if not matches:
            return f"No matches for pattern: {pattern}"
        return "\n".join(matches[:200])
    except _re.error as e:
        return f"Regex error: {e}"


def tool_browser_fd(pattern: str = "", extension: str = None) -> str:
    """
    List links/resources on the current page (like fd but for browser assets).
    Optionally filter by file extension (e.g. 'pdf', 'js') or URL pattern.
    """
    err = _check_open()
    if err:
        return err
    script = """(function(){
        var results = [];
        document.querySelectorAll('a[href], link[href], script[src], img[src], source[src]').forEach(function(el){
            var url = el.href || el.src || '';
            if(url) results.push(url);
        });
        return JSON.stringify(results);
    })()"""
    raw = str(_svc().evaluate(script))
    try:
        urls = json.loads(json.loads(raw)) if raw else []
    except Exception:
        try:
            urls = json.loads(raw)
        except Exception:
            return raw or "No resources found."
    if extension:
        ext = extension.lstrip(".").lower()
        urls = [u for u in urls if u.lower().split("?")[0].endswith("." + ext)]
    if pattern:
        import re as _re
        try:
            urls = [u for u in urls if _re.search(pattern, u)]
        except _re.error as e:
            return f"Regex error: {e}"
    if not urls:
        return "No matching resources found."
    return "\n".join(urls[:200])


BROWSER_OPEN_SPEC = {
    "type": "function",
    "function": {
        "name": "spawn_browser",
        "description": (
            "Open a floating browser window and navigate to a URL. "
            "The browser appears as a draggable overlay on screen. "
            "Returns a DOM snapshot with UIDs you can use to click/fill elements. "
            "After calling this, additional browser control tools become available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open (e.g. 'https://google.com')"},
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
                "Only call this when you need a fresh view of the page. "
                "Do NOT call it after every single click/fill — use browser_batch to perform multiple "
                "actions and include a snapshot step at the end instead."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": (
                "Click an element by UID. Does NOT auto-snapshot after clicking. "
                "To click several things and then see the result, use browser_batch instead — "
                "it lets you sequence click/fill/eval/snapshot in one call."
            ),
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
            "description": "Type a value into an input or textarea by its UID from a snapshot.",
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
            "description": "Execute JavaScript in the open browser and return the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript expression or statement to run"},
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
                "Open a Chrome Custom Tab for the user to log in to a service (Google, GitHub, etc). "
                "Use this when a site blocks WebView login. "
                "The user logs in manually; session cookies carry over to the browser automatically. "
                "After the user closes the login tab, call browser_navigate to continue."
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
            "name": "browser_batch",
            "description": (
                "Run multiple browser actions in a single call — avoids repeated snapshot round-trips. "
                "Use this whenever you need to fill several fields, click a sequence of elements, "
                "or perform any multi-step interaction. "
                "Each action is an object with an 'action' key: "
                "'click' (uid), 'fill' (uid, value), 'eval' (script), "
                "'snapshot', 'navigate' (url), 'wait' (text, timeout_ms). "
                "Returns all results joined. Include a snapshot action at the end to see the final state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "description": "List of actions to execute in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action":     {"type": "string", "description": "Action type: click, fill, eval, snapshot, navigate, wait"},
                                "uid":        {"type": "string", "description": "Element UID (for click/fill)"},
                                "value":      {"type": "string", "description": "Text value (for fill)"},
                                "script":     {"type": "string", "description": "JavaScript (for eval)"},
                                "url":        {"type": "string", "description": "URL (for navigate)"},
                                "text":       {"type": "string", "description": "Text to wait for (for wait)"},
                                "timeout_ms": {"type": "integer", "description": "Timeout ms (for wait, default 10000)"},
                            },
                            "required": ["action"],
                        },
                    },
                },
                "required": ["actions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_rg",
            "description": "Search the current page text using a regex pattern (like ripgrep but on page content). Returns matching lines with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":          {"type": "string",  "description": "Regex pattern to search for in page text"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive search", "default": False},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fd",
            "description": "List links and resources on the current page (like fd but for browser assets: anchors, scripts, images, etc). Filter by extension or URL pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":   {"type": "string", "description": "Optional URL pattern/regex to filter results"},
                    "extension": {"type": "string", "description": "Optional file extension filter, e.g. 'pdf', 'js', 'png'"},
                },
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
