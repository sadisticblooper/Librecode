"""
js_executor.py — Executes .js scripts in the Android WebView via the eval bridge.

Scripts are plain JavaScript files with three async functions:
    async function onLoad()           – navigate + setup
    async function onSend(input)      – type message, click send, wait for response to start
    async function onRead()           – wait for response to finish, return text

The file may also declare:
    const LOAD_URL = "https://example.com";   (optional override; manifest.url takes priority)

Python drives phases by injecting the $oc helper lib + the script once, then calling
  $oc._runPhase("load"|"send"|"read", inputOrNull)
and polling
  $oc._pollDone()
until {done: true}.

For streaming providers (manifest.stream_selector is set):
  - onSend should NOT wait for response to finish — just wait for the new response element.
  - Python calls executor.stream_read(css) which polls $oc._getText(css) and yields deltas.

Public API (mirrors DslExecutor):
    executor = JsExecutor(script_path, manifest, evaluator_fn)
    executor.load()
    executor.send(message)
    reply = executor.read()
    # — streaming variant —
    for chunk in executor.stream_read(css, stable_ms, timeout_ms):
        yield chunk
"""

import json
import os
import re
import time
import logging
from typing import Callable, Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUTS = {
    "ready_ms":         20_000,
    "send_ms":           8_000,
    "response_ms":     120_000,
    "stable_ms":         1_500,
    "poll_interval_ms":    150,
}

# Reuse the CSS conversion from dsl_selector so stream_read can accept
# friendly selectors (role:assistant etc.) for the querySelectorAll call.
def _sel_to_css(sel: str) -> str:
    sel = sel.strip()
    if sel.startswith("aria:"):        return f"[aria-label*='{sel[5:].strip()}']"
    if sel.startswith("placeholder:"): return f"[placeholder*='{sel[12:].strip()}']"
    if sel.startswith("role:"):        return f"[role='{sel[5:].strip()}']"
    if sel.startswith("id:"):          return f"#{sel[3:].strip()}"
    if sel.startswith("css:"):         return sel[4:].strip()
    return sel  # raw CSS or text: — use as-is


# Singleton: load oc_webview.js exactly once per process.
_OC_LIB: Optional[str] = None

def _get_oc_lib() -> str:
    global _OC_LIB
    if _OC_LIB is None:
        lib_path = os.path.join(os.path.dirname(__file__), "oc_webview.js")
        with open(lib_path, "r", encoding="utf-8") as f:
            _OC_LIB = f.read()
    return _OC_LIB


class JsError(RuntimeError):
    """Raised when a script phase fails (mirrors DslError for provider compatibility)."""
    pass


class _BridgeTimeout:
    """Singleton sentinel — Android never responded to an eval."""
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self): return "<BridgeTimeout>"

BRIDGE_TIMEOUT = _BridgeTimeout()


class JsExecutor:
    def __init__(
        self,
        script_path: str,
        manifest: dict,
        evaluator: Callable[[str], Optional[str]],
    ):
        self.script_path = script_path
        self.manifest    = manifest
        self._eval       = evaluator
        self.timeouts    = {**DEFAULT_TIMEOUTS, **manifest.get("timeouts", {})}
        self._injected   = False

        self._script_js: str = ""
        self._load_url: Optional[str] = manifest.get("url", "")
        self._read_script()

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _read_script(self):
        with open(self.script_path, "r", encoding="utf-8") as f:
            self._script_js = f.read()
        # Let the script override the URL with  const LOAD_URL = "..."
        m = re.search(r'const\s+LOAD_URL\s*=\s*["\']([^"\']+)["\']', self._script_js)
        if m:
            self._load_url = m.group(1)

    def _ensure_injected(self):
        """Inject $oc lib + user script into the WebView (idempotent per session)."""
        if self._injected:
            return
        lib = _get_oc_lib()
        full = lib + "\n;\n" + self._script_js
        result = self._eval_raw(full)
        if result is BRIDGE_TIMEOUT:
            raise JsError("Bridge timeout during $oc injection — Android not polling.")
        # Verify the lib loaded correctly.
        check = self._eval_raw("window.__ocLoaded === true ? 'yes' : 'no'")
        if check != "yes":
            raise JsError(f"$oc injection failed (check returned: {check!r}).")
        self._injected = True
        logger.info("[js_exec] $oc injected for %s", self.manifest.get("id", "?"))

    def _reset_injection(self):
        """Call when the WebView navigates away and the context is lost."""
        self._injected = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self):
        """Navigate to the target URL, then run onLoad()."""
        if self._load_url:
            self._eval_raw(f"window.location.href = {json.dumps(self._load_url)};")
            # Brief pause for page to start loading before we inject scripts.
            time.sleep(0.8)
        self._reset_injection()
        self._ensure_injected()
        self._run_phase("load", None, self.timeouts["ready_ms"])

    def send(self, message: str):
        """Run onSend(message). For streaming scripts this returns once the
        response element appears; for non-streaming it returns when done."""
        self._ensure_injected()
        self._run_phase("send", message, self.timeouts["send_ms"] + self.timeouts["response_ms"])

    def read(self) -> str:
        """Run onRead() and return the extracted text."""
        self._ensure_injected()
        result = self._run_phase("read", None, self.timeouts["response_ms"])
        return result or ""

    def stream_read(
        self,
        selector: str,
        stable_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
    ) -> Iterator[str]:
        """
        Generator: polls the last element matching *selector* and yields
        incremental text chunks as the response is written.

        Stops when the text hasn't changed for *stable_ms* milliseconds.
        Use this AFTER send() returns (response element has appeared).

        selector can be a friendly DSL selector (role:assistant) or raw CSS.
        """
        css         = _sel_to_css(selector)
        stable_ms   = stable_ms  or self.timeouts.get("stream_stable_ms",
                                                       self.timeouts["stable_ms"])
        timeout_ms  = timeout_ms or self.timeouts["response_ms"]
        interval_ms = self.timeouts["poll_interval_ms"]

        poll_js = (
            f"(function(){{"
            f"  var all = document.querySelectorAll({json.dumps(css)});"
            f"  if (!all.length) return null;"
            f"  return all[all.length-1].innerText || null;"
            f"}})()"
        )

        deadline     = time.time() + timeout_ms / 1000
        last_text    = ""
        stable_since: Optional[float] = None
        bridge_alive = False

        while time.time() < deadline:
            raw = self._eval_raw(poll_js)

            if raw is BRIDGE_TIMEOUT:
                time.sleep(interval_ms / 1000)
                continue

            bridge_alive = True
            text = raw or ""

            if text != last_text:
                if len(text) > len(last_text):
                    yield text[len(last_text):]
                last_text    = text
                stable_since = time.time()
            else:
                if stable_since is not None and (time.time() - stable_since) * 1000 >= stable_ms:
                    break  # response is stable — done

            time.sleep(interval_ms / 1000)

        if not bridge_alive:
            raise JsError(
                "Bridge timeout during stream_read — Android not polling /mediator/eval."
            )

    def stream_read_with_thinking(
        self,
        selector: str,
        thinking_js: Optional[str] = None,
        stable_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
    ) -> Iterator[dict]:
        """
        Generator that yields dicts with keys:
          {"type": "thinking", "text": delta}  — incremental thinking/reasoning tokens
          {"type": "text",     "text": delta}  — incremental reply tokens

        Uses window._ocGetSnapshot() if available (injected by script.js),
        otherwise falls back to plain stream_read() yielding only text deltas.

        Stops when both reply and thinking have been stable for stable_ms AND
        the page reports generating=False (stop button gone).
        """
        css         = _sel_to_css(selector)
        stable_ms   = stable_ms  or self.timeouts.get("stream_stable_ms",
                                                       self.timeouts["stable_ms"])
        timeout_ms  = timeout_ms or self.timeouts["response_ms"]
        interval_ms = self.timeouts["poll_interval_ms"]

        # JS snippet to call: uses _ocGetSnapshot if available, else plain poll.
        snapshot_js = thinking_js or "window._ocGetSnapshot ? window._ocGetSnapshot() : null"

        # Fallback plain-text poll (when snapshot unavailable)
        plain_poll_js = (
            f"(function(){{"
            f"  var all = document.querySelectorAll({json.dumps(css)});"
            f"  if (!all.length) return null;"
            f"  return all[all.length-1].innerText || null;"
            f"}})()"
        )

        deadline      = time.time() + timeout_ms / 1000
        last_thinking = ""
        last_reply    = ""
        stable_since: Optional[float] = None
        bridge_alive  = False

        while time.time() < deadline:
            raw = self._eval_raw(snapshot_js)

            if raw is BRIDGE_TIMEOUT:
                time.sleep(interval_ms / 1000)
                continue

            bridge_alive = True

            # Parse snapshot JSON
            snapshot = None
            if raw:
                try:
                    import json as _json
                    snapshot = _json.loads(raw)
                except Exception:
                    pass

            if snapshot is None:
                # _ocGetSnapshot not present — fall back to plain text
                text = self._eval_raw(plain_poll_js) or ""
                if isinstance(text, str) and text != last_reply:
                    if len(text) > len(last_reply):
                        yield {"type": "text", "text": text[len(last_reply):]}
                    last_reply    = text
                    stable_since  = time.time()
                else:
                    if stable_since and (time.time() - stable_since) * 1000 >= stable_ms:
                        break
                time.sleep(interval_ms / 1000)
                continue

            thinking  = snapshot.get("thinking") or ""
            reply     = snapshot.get("reply")    or ""
            generating = snapshot.get("generating", True)

            changed = False

            # Emit new thinking delta
            if thinking and len(thinking) > len(last_thinking):
                yield {"type": "thinking", "text": thinking[len(last_thinking):]}
                last_thinking = thinking
                changed = True

            # Emit new reply delta
            if reply and len(reply) > len(last_reply):
                yield {"type": "text", "text": reply[len(last_reply):]}
                last_reply = reply
                changed = True

            if changed:
                stable_since = time.time()
            else:
                # Stable check: done when not generating AND text hasn't moved
                if not generating:
                    if stable_since is not None and (time.time() - stable_since) * 1000 >= stable_ms:
                        break
                    elif stable_since is None:
                        stable_since = time.time()

            time.sleep(interval_ms / 1000)

        if not bridge_alive:
            raise JsError(
                "Bridge timeout during stream_read_with_thinking — Android not polling /mediator/eval."
            )

    # ── Phase runner ───────────────────────────────────────────────────────────

    def _run_phase(
        self,
        phase: str,
        input_val: Optional[str],
        timeout_ms: int,
    ) -> Optional[str]:
        """
        Start a WebView phase and poll until it completes.
        Returns the phase result string, or raises JsError on failure/timeout.
        """
        input_js = json.dumps(input_val) if input_val is not None else "null"
        start_js = f"$oc._runPhase({json.dumps(phase)}, {input_js}); 'started'"

        raw = self._eval_raw(start_js)
        if raw is BRIDGE_TIMEOUT:
            raise JsError(f"Bridge timeout starting phase '{phase}'.")

        # _runPhase() is async — poll _pollDone() until {done: true}.
        interval_ms = self.timeouts["poll_interval_ms"]
        deadline    = time.time() + timeout_ms / 1000
        bridge_alive = False

        while time.time() < deadline:
            poll_raw = self._eval_raw("$oc._pollDone()")
            if poll_raw is BRIDGE_TIMEOUT:
                time.sleep(interval_ms / 1000)
                continue

            bridge_alive = True
            try:
                state = json.loads(poll_raw)
            except (json.JSONDecodeError, TypeError):
                time.sleep(interval_ms / 1000)
                continue

            if state.get("done"):
                err = state.get("error")
                if err:
                    raise JsError(f"[{phase}] {err}")
                return state.get("result")

            time.sleep(interval_ms / 1000)

        if not bridge_alive:
            raise JsError(
                f"Bridge timeout ({timeout_ms}ms) in phase '{phase}' — "
                f"Android not polling /mediator/eval."
            )

        raise JsError(
            f"Phase '{phase}' timed out after {timeout_ms}ms. "
            f"WebView may still be running. "
            f"URL: {self._eval_raw('window.location.href') or '?'}"
        )

    # ── Eval wrapper ───────────────────────────────────────────────────────────

    def _eval_raw(self, js: str):
        """
        Returns:
          str            — bridge alive, JS returned a value
          None           — bridge alive, JS returned null/undefined
          BRIDGE_TIMEOUT — Android never responded
        """
        try:
            return self._eval(js)
        except Exception as e:
            logger.error("[js_exec] eval error (bridge timeout?): %s", e)
            return BRIDGE_TIMEOUT
