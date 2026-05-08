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
            "description": "Get the current DOM tree of the open browser with UIDs for all interactive elements. Use UIDs to target elements with click/fill.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element in the browser by its UID from a snapshot. Returns updated snapshot.",
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
            "name": "browser_close",
            "description": "Close the floating browser window and end the browser session.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
