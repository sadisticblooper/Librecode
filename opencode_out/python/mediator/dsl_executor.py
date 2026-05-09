"""
dsl_executor.py — Parses and executes .oc DSL scripts.

This runs entirely in Python/Flask. It communicates with a headless WebView
(MediatorWebView on Android) via the /mediator/eval endpoint that the
Java side exposes, OR falls back to a mock evaluator for testing.

Public API
----------
executor = DslExecutor(script_dir, manifest, evaluator_fn)
executor.load()                  # executes LOAD
executor.send("user message")    # executes ON SEND block
reply = executor.read()          # executes ON READ block
"""

import re
import time
import logging
from typing import Callable, Optional

from .dsl_selector import (
    click_js, type_js, wait_for_js, wait_while_js,
    extract_js, if_visible_finder_js,
)

logger = logging.getLogger(__name__)

# ── Defaults (overridden by manifest) ─────────────────────────────────────────
DEFAULT_TIMEOUTS = {
    "ready_ms":       8_000,
    "send_ms":        5_000,
    "response_ms":  120_000,
    "stable_ms":      1_500,
    "poll_interval_ms": 600,
}


class DslError(RuntimeError):
    pass


class DslExecutor:
    """
    Loads and executes a .oc script file.

    Parameters
    ----------
    script_path : str
        Absolute path to the script.oc file.
    manifest : dict
        Parsed manifest.json for this provider.
    evaluator : Callable[[str], Optional[str]]
        Function that runs JS in the WebView and returns the string result
        (or None on timeout / null return). This is provided by the
        MediatorServer and wired to the Android WebView bridge.
    """

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

        self._on_send_lines: list[str] = []
        self._on_read_lines: list[str] = []
        self._load_url: Optional[str]  = None

        self._parse()

    # ── Parsing ────────────────────────────────────────────────────────────────

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
            elif upper == "ON SEND":
                section = "send"
            elif upper == "ON READ":
                section = "read"
            elif upper == "END":
                section = None
            elif section == "send":
                self._on_send_lines.append(line)
            elif section == "read":
                self._on_read_lines.append(line)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self):
        """Navigate the WebView to the provider URL."""
        if self._load_url:
            self._eval_raw(f"window.location.href = {repr(self._load_url)};")

    def send(self, message: str):
        """Execute ON SEND block with $INPUT = message."""
        for line in self._on_send_lines:
            self._run_line(line, input_val=message,
                           timeout_ms=self.timeouts["send_ms"])

    def read(self) -> str:
        """Execute ON READ block and return the extracted reply."""
        for line in self._on_read_lines:
            result = self._run_line(line, input_val=None,
                                    timeout_ms=self.timeouts["response_ms"])
            if result is not None:
                return result
        return ""

    # ── Command dispatcher ─────────────────────────────────────────────────────

    def _run_line(self, line: str, input_val: Optional[str],
                  timeout_ms: int) -> Optional[str]:
        # Substitute $INPUT
        if input_val is not None:
            line = line.replace("$INPUT", input_val)

        upper = line.upper()

        # WAIT_FOR <selector>
        if upper.startswith("WAIT_FOR "):
            sel = line[9:].strip()
            js  = wait_for_js(sel)
            self._poll(js, "FOUND", timeout_ms)

        # WAIT_WHILE <selector>
        elif upper.startswith("WAIT_WHILE "):
            sel = line[11:].strip()
            js  = wait_while_js(sel)
            self._poll(js, "GONE", timeout_ms)

        # CLICK <selector>
        elif upper.startswith("CLICK "):
            sel = line[6:].strip()
            js  = click_js(sel)
            res = self._eval_raw(js)
            if res == "NOT_FOUND":
                logger.warning("CLICK: element not found — %s", sel)

        # TYPE <selector> WITH <value>
        elif upper.startswith("TYPE "):
            m = re.match(r'TYPE\s+(.+?)\s+WITH\s+(.+)', line, re.IGNORECASE)
            if m:
                sel, val = m.group(1).strip(), m.group(2).strip()
                # val may still contain $INPUT substituted already
                js  = type_js(sel, val)
                res = self._eval_raw(js)
                if res == "NOT_FOUND":
                    logger.warning("TYPE: element not found — %s", sel)

        # EXTRACT <strategy>
        elif upper.startswith("EXTRACT "):
            strategy = line[8:].strip()
            js       = extract_js(strategy)
            return self._eval_raw(js)

        # NAVIGATE <url>
        elif upper.startswith("NAVIGATE "):
            url = line[9:].strip()
            self._eval_raw(f"window.location.href = {repr(url)};")

        # EVAL <js>
        elif upper.startswith("EVAL "):
            js = line[5:].strip()
            return self._eval_raw(js)

        # IF_VISIBLE <selector> THEN <command>
        elif upper.startswith("IF_VISIBLE "):
            m = re.match(r'IF_VISIBLE\s+(.+?)\s+THEN\s+(.+)', line, re.IGNORECASE)
            if m:
                sel, cmd = m.group(1).strip(), m.group(2).strip()
                vis_js   = if_visible_finder_js(sel)
                vis      = self._eval_raw(vis_js)
                if vis == "VISIBLE":
                    return self._run_line(cmd, input_val, timeout_ms)

        # RETURN ERROR:<reason>
        elif upper.startswith("RETURN ERROR:"):
            reason = line[13:].strip()
            raise DslError(reason)

        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _poll(self, js: str, expected: str, timeout_ms: int):
        interval_ms = self.timeouts["poll_interval_ms"]
        deadline    = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            result = self._eval_raw(js)
            if result == expected:
                return
            time.sleep(interval_ms / 1000)
        raise DslError(f"Timeout waiting for '{expected}' — JS: {js[:80]}")

    def _eval_raw(self, js: str) -> Optional[str]:
        try:
            return self._eval(js)
        except Exception as e:
            logger.error("eval error: %s", e)
            return None
