"""
dsl_executor.py - Parses and executes .oc DSL scripts.

Public API:
  executor = DslExecutor(script_path, manifest, evaluator_fn)
  executor.load()        # LOAD + ON LOAD block
  executor.send(msg)     # ON SEND block
  reply = executor.read()# ON READ block
"""

import re
import time
import logging
from typing import Callable, Optional

from .dsl_selector import (
    click_js, type_js, clear_js, press_key_js,
    wait_for_js, wait_while_js, wait_stable_js, wait_url_js, wait_count_js,
    extract_js, if_visible_finder_js,
    scroll_to_js, scroll_page_js,
    select_option_js, hover_js, get_attr_js,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUTS = {
    "ready_ms":         20_000,
    "send_ms":           8_000,
    "response_ms":     120_000,
    "stable_ms":         1_500,
    "poll_interval_ms":    150,
}


class DslError(RuntimeError):
    pass


class _BridgeTimeout:
    """Sentinel: Android never responded (distinct from None = JS returned null)."""
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self): return "<BridgeTimeout>"

BRIDGE_TIMEOUT = _BridgeTimeout()


class DslExecutor:
    def __init__(self, script_path: str, manifest: dict,
                 evaluator: Callable[[str], Optional[str]]):
        self.script_path = script_path
        self.manifest    = manifest
        self._eval       = evaluator
        self.timeouts    = {**DEFAULT_TIMEOUTS, **manifest.get("timeouts", {})}

        self._on_load_lines: list[str] = []
        self._on_send_lines: list[str] = []
        self._on_read_lines: list[str] = []
        self._load_url: Optional[str]  = None
        self._parse()

    # ── Parse ──────────────────────────────────────────────────────────────────

    def _parse(self):
        with open(self.script_path, "r", encoding="utf-8") as f:
            raw = f.read()
        section = None
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            upper = line.upper()
            if upper.startswith("LOAD "):
                self._load_url = line[5:].strip()
            elif upper == "ON LOAD":    section = "load"
            elif upper == "ON SEND":    section = "send"
            elif upper == "ON READ":    section = "read"
            elif upper == "END":        section = None
            elif section == "load":     self._on_load_lines.append(line)
            elif section == "send":     self._on_send_lines.append(line)
            elif section == "read":     self._on_read_lines.append(line)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self):
        if self._load_url:
            self._eval_raw(f"window.location.href={repr(self._load_url)};")
        for line in self._on_load_lines:
            self._run_line(line, None, self.timeouts["ready_ms"])

    def send(self, message: str):
        for line in self._on_send_lines:
            self._run_line(line, message, self.timeouts["send_ms"])

    def read(self) -> str:
        for line in self._on_read_lines:
            result = self._run_line(line, None, self.timeouts["response_ms"])
            if result is not None:
                return result
        return ""

    # ── Command dispatcher ─────────────────────────────────────────────────────

    def _run_line(self, line: str, input_val: Optional[str],
                  timeout_ms: int) -> Optional[str]:
        if input_val is not None:
            line = line.replace("$INPUT", input_val)
        upper = line.upper()

        # ── Waiting ────────────────────────────────────────────────────────────

        if upper.startswith("WAIT_FOR "):
            sel = line[9:].strip()
            self._poll(wait_for_js(sel), "FOUND", timeout_ms,
                err=f"WAIT_FOR '{sel}' — element never became visible after {timeout_ms}ms.")

        elif upper.startswith("WAIT_WHILE "):
            sel = line[11:].strip()
            def _wait_while_err(sel=sel):
                aria = self._get_aria_labels()
                btns = self._eval_raw(
                    'Array.from(document.querySelectorAll("button"))'
                    '.map(b=>b.innerText.trim()).filter(Boolean).slice(0,20).join(" | ")'
                ) or "none"
                url = self._eval_raw("window.location.href") or "?"
                return (
                    f"WAIT_WHILE '{sel}' -- never appeared. "
                    f"Aria-labels: {aria}. "
                    f"Button texts: {btns}. "
                    f"URL: {url}"
                )
            self._poll(wait_for_js(sel), "FOUND", self.timeouts["send_ms"], err=_wait_while_err)
            self._poll(wait_while_js(sel), "GONE", timeout_ms,
                err=f"WAIT_WHILE '{sel}' -- still visible after {timeout_ms}ms. Page may still be streaming.")

        elif upper.startswith("WAIT_URL "):
            pattern = line[9:].strip()
            self._poll(wait_url_js(pattern), "MATCHED", timeout_ms,
                err=lambda: (
                    f"WAIT_URL '{pattern}' — URL never matched after {timeout_ms}ms. "
                    f"Current: {self._eval_raw('window.location.href') or '?'}"
                ))

        elif upper.startswith("WAIT_STABLE "):
            self._poll_stable(line[12:].strip(), timeout_ms)

        elif upper.startswith("WAIT_COUNT "):
            m = re.match(r'WAIT_COUNT\s+(.+?)\s+(gt|gte|lt|lte|eq)\s+(\d+)', line, re.IGNORECASE)
            if m:
                sel, op, n = m.group(1).strip(), m.group(2).lower(), int(m.group(3))
                self._poll(wait_count_js(sel, op, n), "MET", timeout_ms,
                    err=lambda: (
                        f"WAIT_COUNT '{sel}' {op} {n} — not met after {timeout_ms}ms. "
                        f"Current count: {self._eval_raw(f'String(document.querySelectorAll({repr(sel)}).length)') or '?'}"
                    ))

        # ── Interaction ────────────────────────────────────────────────────────

        elif upper.startswith("CLICK "):
            sel = line[6:].strip()
            res = self._eval_raw(click_js(sel))
            if res is BRIDGE_TIMEOUT:
                raise DslError(f"CLICK '{sel}' — bridge timeout.")
            if res == "NOT_FOUND":
                raise DslError(
                    f"CLICK '{sel}' — element not found. "
                    f"Aria-labels: {self._get_aria_labels()}. "
                    f"URL: {self._eval_raw('window.location.href') or '?'}"
                )

        elif upper.startswith("TYPE "):
            m = re.match(r'TYPE\s+(.+?)\s+WITH\s+(.+)', line, re.IGNORECASE)
            if m:
                sel, val = m.group(1).strip(), m.group(2).strip()
                res = self._eval_raw(type_js(sel, val))
                if res is BRIDGE_TIMEOUT:
                    raise DslError(f"TYPE '{sel}' — bridge timeout.")
                if res == "NOT_FOUND":
                    raise DslError(
                        f"TYPE '{sel}' — input not found. "
                        f"Placeholders: {self._get_placeholders()}. "
                        f"URL: {self._eval_raw('window.location.href') or '?'}"
                    )

        elif upper.startswith("CLEAR "):
            sel = line[6:].strip()
            res = self._eval_raw(clear_js(sel))
            if res is BRIDGE_TIMEOUT:
                raise DslError(f"CLEAR '{sel}' — bridge timeout.")
            if res == "NOT_FOUND":
                raise DslError(f"CLEAR '{sel}' — element not found. URL: {self._eval_raw('window.location.href') or '?'}")

        elif upper.startswith("PRESS_KEY "):
            m = re.match(r'PRESS_KEY\s+(.+?)\s+(\S+)$', line, re.IGNORECASE)
            if m:
                sel, key = m.group(1).strip(), m.group(2).strip()
                res = self._eval_raw(press_key_js(sel, key))
                if res is BRIDGE_TIMEOUT:
                    raise DslError(f"PRESS_KEY '{sel}' {key} — bridge timeout.")
                if res == "NOT_FOUND":
                    raise DslError(f"PRESS_KEY '{sel}' {key} — element not found.")

        elif upper.startswith("HOVER "):
            sel = line[6:].strip()
            res = self._eval_raw(hover_js(sel))
            if res is BRIDGE_TIMEOUT:
                raise DslError(f"HOVER '{sel}' — bridge timeout.")
            if res == "NOT_FOUND":
                logger.warning("HOVER: '%s' not found (non-fatal)", sel)

        elif upper.startswith("SELECT "):
            m = re.match(r'SELECT\s+(.+?)\s+WITH\s+(.+)', line, re.IGNORECASE)
            if m:
                sel, val = m.group(1).strip(), m.group(2).strip()
                res = self._eval_raw(select_option_js(sel, val))
                if res is BRIDGE_TIMEOUT:
                    raise DslError(f"SELECT '{sel}' — bridge timeout.")
                if res == "NOT_FOUND":
                    raise DslError(f"SELECT '{sel}' — dropdown not found.")
                if res == "OPTION_NOT_FOUND":
                    raise DslError(f"SELECT '{sel}' — option '{val}' not in dropdown.")

        # ── Navigation ─────────────────────────────────────────────────────────

        elif upper.startswith("NAVIGATE "):
            self._eval_raw(f"window.location.href={repr(line[9:].strip())};")

        elif upper == "NAVIGATE_BACK":
            self._eval_raw("window.history.back();")

        elif upper == "NAVIGATE_FORWARD":
            self._eval_raw("window.history.forward();")

        elif upper == "RELOAD":
            self._eval_raw("window.location.reload();")

        elif upper.startswith("SCROLL_TO "):
            sel = line[10:].strip()
            res = self._eval_raw(scroll_to_js(sel))
            if res is BRIDGE_TIMEOUT:
                raise DslError(f"SCROLL_TO '{sel}' — bridge timeout.")
            if res == "NOT_FOUND":
                logger.warning("SCROLL_TO: '%s' not found (non-fatal)", sel)

        elif upper.startswith("SCROLL "):
            parts = line[7:].strip().split()
            direction = parts[0].lower()
            amount = int(parts[1]) if len(parts) > 1 else 300
            self._eval_raw(scroll_page_js(direction, amount))

        # ── Extraction ─────────────────────────────────────────────────────────

        elif upper.startswith("EXTRACT "):
            return self._eval_raw(extract_js(line[8:].strip()))

        # ── Control flow ───────────────────────────────────────────────────────

        elif upper.startswith("IF_VISIBLE "):
            m = re.match(r'IF_VISIBLE\s+(.+?)\s+THEN\s+(.+)', line, re.IGNORECASE)
            if m:
                sel, cmd = m.group(1).strip(), m.group(2).strip()
                vis = self._eval_raw(if_visible_finder_js(sel))
                if vis is not BRIDGE_TIMEOUT and vis == "VISIBLE":
                    return self._run_line(cmd, input_val, timeout_ms)

        elif upper.startswith("IF_URL "):
            m = re.match(r'IF_URL\s+(.+?)\s+THEN\s+(.+)', line, re.IGNORECASE)
            if m:
                pattern, cmd = m.group(1).strip(), m.group(2).strip()
                url = self._eval_raw("window.location.href") or ""
                if pattern in url:
                    return self._run_line(cmd, input_val, timeout_ms)

        elif upper.startswith("RETURN ERROR:"):
            raise DslError(line[13:].strip())

        elif upper.startswith("EVAL "):
            return self._eval_raw(line[5:].strip())

        elif upper.startswith("SLEEP "):
            time.sleep(int(line[6:].strip()) / 1000)

        return None

    # ── Polling ────────────────────────────────────────────────────────────────

    def _poll(self, js: str, expected: str, timeout_ms: int, err=None):
        interval_ms  = self.timeouts["poll_interval_ms"]
        deadline     = time.time() + timeout_ms / 1000
        bridge_alive = False
        last_result  = "<no response>"

        while time.time() < deadline:
            raw = self._eval_raw(js)
            if raw is BRIDGE_TIMEOUT:
                pass
            else:
                bridge_alive = True
                last_result  = repr(raw)
                if raw == expected:
                    return
            time.sleep(interval_ms / 1000)

        if not bridge_alive:
            raise DslError(
                f"Bridge timeout ({timeout_ms}ms): Android never responded to JS eval. "
                f"MediatorBridgePoller is not running or not polling /mediator/eval for this script."
            )

        if err is not None:
            raise DslError(err() if callable(err) else err)

        raise DslError(
            f"Selector not found after {timeout_ms}ms (last eval: {last_result}). "
            f"{self._diagnostics(js)}"
        )

    def _poll_stable(self, selector: str, timeout_ms: int):
        """Wait until element innerText stops changing for stable_ms."""
        js           = wait_stable_js(selector)
        stable_ms    = self.timeouts["stable_ms"]
        interval_ms  = self.timeouts["poll_interval_ms"]
        deadline     = time.time() + timeout_ms / 1000
        last_text    = None
        stable_since: Optional[float] = None

        while time.time() < deadline:
            raw = self._eval_raw(js)
            if raw is BRIDGE_TIMEOUT:
                time.sleep(interval_ms / 1000)
                continue
            if raw != last_text:
                last_text    = raw
                stable_since = time.time()
            elif stable_since and (time.time() - stable_since) * 1000 >= stable_ms:
                return
            time.sleep(interval_ms / 1000)

        raise DslError(
            f"WAIT_STABLE '{selector}' — content never stabilised after {timeout_ms}ms. "
            f"Response may still be streaming."
        )

    # ── Eval ───────────────────────────────────────────────────────────────────

    def _eval_raw(self, js: str):
        """
        Returns:
          str           - bridge alive, JS returned a value
          None          - bridge alive, JS returned null
          BRIDGE_TIMEOUT - bridge never responded
        """
        try:
            return self._eval(js)
        except Exception as e:
            logger.error("eval error (bridge timeout?): %s", e)
            return BRIDGE_TIMEOUT

    # ── Diagnostics ────────────────────────────────────────────────────────────

    def _diagnostics(self, failing_js: str) -> str:
        lines = []
        url   = self._eval_raw("window.location.href")
        title = self._eval_raw("document.title")
        if url and url is not BRIDGE_TIMEOUT:
            lines.append(f"URL={url}")
        if title and title is not BRIDGE_TIMEOUT:
            lines.append(f"title={title}")

        placeholders = self._get_placeholders()
        aria_labels  = self._get_aria_labels()
        if placeholders != "none":
            lines.append(f"placeholders=[{placeholders}]")
        if aria_labels != "none":
            lines.append(f"aria-labels=[{aria_labels}]")

        # Count how many matches the failing selector has (ignoring visibility)
        sel_match = re.search(r"querySelector(?:All)?\('([^']+)'\)", failing_js)
        if sel_match:
            css = sel_match.group(1)
            count = self._eval_raw(f"document.querySelectorAll('{css}').length")
            if count and count is not BRIDGE_TIMEOUT:
                lines.append(f"matches for '{css}'={count}")

        return " | ".join(lines)

    def _get_placeholders(self) -> str:
        r = self._eval_raw(
            "(function(){var e=document.querySelectorAll('[placeholder]');"
            "return Array.from(e).map(function(x){return x.placeholder;}).join(' | ')||'none';})()"
        )
        return r if r and r is not BRIDGE_TIMEOUT else "none"

    def _get_aria_labels(self) -> str:
        r = self._eval_raw(
            "(function(){var e=document.querySelectorAll('[aria-label]');"
            "var l=Array.from(e).map(function(x){return x.getAttribute('aria-label');}).filter(Boolean);"
            "return l.slice(0,15).join(' | ')||'none';})()"
        )
        return r if r and r is not BRIDGE_TIMEOUT else "none"
