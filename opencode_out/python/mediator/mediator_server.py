"""
mediator_server.py — OpenAI-compatible localhost endpoint for DSL-scripted providers.

Registers these routes on the main Flask app:

  POST /mediator/start/<script_id>          Boot a script session
  POST /mediator/stop/<script_id>           Tear down a session
  GET  /mediator/sessions                   List active sessions
  GET  /mediator/scripts                    List available scripts in opencode/scripts/
  GET  /mediator/scripts/<script_id>        Read a script.oc
  PUT  /mediator/scripts/<script_id>        Write/update a script.oc
  POST /mediator/eval/<script_id>           Bridge: Android calls this to deliver JS eval results
  POST /v1/chat/completions/<script_id>     OpenAI-compat chat endpoint
"""

import json
import os
import uuid
import threading
import time
import logging
from typing import Optional

from flask import Flask, request, jsonify, Response, stream_with_context

from .dsl_executor import DslExecutor, DslError
from .tool_harness import wrap as harness_wrap, parse as harness_parse
from .script_session import session_manager

logger = logging.getLogger(__name__)

# ── JS eval bridge ─────────────────────────────────────────────────────────────
# Android WebView posts results here; Python side blocks waiting for them.
_eval_pending: dict[str, threading.Event]   = {}   # req_id → Event
_eval_results: dict[str, Optional[str]]     = {}   # req_id → result string
_eval_lock = threading.Lock()

# Per-script_id queue of pending JS eval requests for the Android side to pick up
_eval_requests: dict[str, list[dict]] = {}  # script_id → [{id, js}, ...]
_eval_req_lock = threading.Lock()


def _build_evaluator(script_id: str, timeout_sec: float = 30.0):
    """Returns an evaluator function wired to the Android bridge."""
    def evaluator(js: str) -> Optional[str]:
        req_id = str(uuid.uuid4())
        evt    = threading.Event()

        with _eval_lock:
            _eval_pending[req_id] = evt
            _eval_results[req_id] = None

        with _eval_req_lock:
            _eval_requests.setdefault(script_id, []).append({"id": req_id, "js": js})

        got = evt.wait(timeout=timeout_sec)

        with _eval_lock:
            result = _eval_results.pop(req_id, None)
            _eval_pending.pop(req_id, None)

        if not got:
            logger.warning("eval timeout for req %s", req_id)
            return None
        return result

    return evaluator


# ── Loaded executors cache ─────────────────────────────────────────────────────
_executors: dict[str, DslExecutor] = {}
_exec_lock = threading.Lock()


def _get_scripts_dir() -> str:
    from python.storage import get_opencode_dir
    d = os.path.join(get_opencode_dir(), "scripts")
    os.makedirs(d, exist_ok=True)
    return d


def _load_manifest(script_id: str) -> Optional[dict]:
    path = os.path.join(_get_scripts_dir(), script_id, "manifest.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_executor(script_id: str) -> Optional[DslExecutor]:
    with _exec_lock:
        if script_id in _executors:
            return _executors[script_id]

    manifest = _load_manifest(script_id)
    if not manifest:
        return None

    script_name = manifest.get("script", "script.oc")
    script_path = os.path.join(_get_scripts_dir(), script_id, script_name)
    if not os.path.isfile(script_path):
        return None

    evaluator = _build_evaluator(script_id, timeout_sec=manifest.get("timeouts", {}).get("response_ms", 120_000) / 1000)
    executor  = DslExecutor(script_path, manifest, evaluator)

    with _exec_lock:
        _executors[script_id] = executor
    return executor


def _invalidate_executor(script_id: str):
    with _exec_lock:
        _executors.pop(script_id, None)


# ── Seed default scripts if missing ───────────────────────────────────────────

def seed_default_scripts():
    """Copy bundled scripts/ folder into opencode/scripts/ if not already present."""
    import shutil
    scripts_dir = _get_scripts_dir()
    # Bundled scripts live next to this file's package root: opencode_out/scripts/
    bundle_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts")
    bundle_dir = os.path.normpath(bundle_dir)
    if not os.path.isdir(bundle_dir):
        return
    for name in os.listdir(bundle_dir):
        src = os.path.join(bundle_dir, name)
        dst = os.path.join(scripts_dir, name)
        if os.path.isdir(src) and not os.path.isdir(dst):
            shutil.copytree(src, dst)


# ── Route registration ─────────────────────────────────────────────────────────

def register_mediator_routes(app: Flask):
    """Call from routes.py init_app to mount all mediator routes."""

    seed_default_scripts()

    # ── Script management ──────────────────────────────────────────────────────

    @app.route("/mediator/scripts", methods=["GET"])
    def list_scripts():
        scripts_dir = _get_scripts_dir()
        results = []
        for name in sorted(os.listdir(scripts_dir)):
            folder = os.path.join(scripts_dir, name)
            if not os.path.isdir(folder):
                continue
            manifest = _load_manifest(name) or {}
            script_file = manifest.get("script", "script.oc")
            script_path = os.path.join(folder, script_file)
            results.append({
                "id":           name,
                "label":        manifest.get("label", name),
                "url":          manifest.get("url", ""),
                "port":         manifest.get("port", 0),
                "supports_tools": manifest.get("supports_tools", False),
                "script_file":  script_file,
                "has_script":   os.path.isfile(script_path),
                "folder":       folder,
            })
        return jsonify({"scripts": results, "scripts_dir": scripts_dir})

    @app.route("/mediator/scripts/<script_id>", methods=["GET"])
    def get_script(script_id: str):
        manifest = _load_manifest(script_id)
        if not manifest:
            return jsonify({"error": "Script not found"}), 404
        script_file = manifest.get("script", "script.oc")
        script_path = os.path.join(_get_scripts_dir(), script_id, script_file)
        content = ""
        if os.path.isfile(script_path):
            with open(script_path, "r", encoding="utf-8") as f:
                content = f.read()
        return jsonify({"id": script_id, "manifest": manifest, "content": content})

    @app.route("/mediator/scripts/<script_id>", methods=["PUT"])
    def update_script(script_id: str):
        """Update script.oc content. Also accepts manifest updates."""
        data     = request.json or {}
        scripts_dir = _get_scripts_dir()
        folder   = os.path.join(scripts_dir, script_id)
        os.makedirs(folder, exist_ok=True)

        # Write manifest if provided
        if "manifest" in data:
            with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(data["manifest"], f, indent=2)

        # Write script content if provided
        if "content" in data:
            manifest    = _load_manifest(script_id) or {}
            script_file = manifest.get("script", "script.oc")
            with open(os.path.join(folder, script_file), "w", encoding="utf-8") as f:
                f.write(data["content"])

        # Invalidate cached executor so it reloads fresh
        _invalidate_executor(script_id)
        return jsonify({"status": "ok"})

    @app.route("/mediator/scripts/<script_id>", methods=["DELETE"])
    def delete_script(script_id: str):
        import shutil
        folder = os.path.join(_get_scripts_dir(), script_id)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        _invalidate_executor(script_id)
        session_manager.invalidate(script_id)
        return jsonify({"status": "ok"})

    @app.route("/mediator/scripts/<script_id>/create", methods=["POST"])
    def create_script(script_id: str):
        """Create a new script folder with a blank template."""
        data        = request.json or {}
        scripts_dir = _get_scripts_dir()
        folder      = os.path.join(scripts_dir, script_id)
        if os.path.isdir(folder):
            return jsonify({"error": "Already exists"}), 409
        os.makedirs(folder, exist_ok=True)
        manifest = {
            "id":    script_id,
            "label": data.get("label", script_id),
            "url":   data.get("url", ""),
            "port":  data.get("port", 11440),
            "supports_tools": False,
            "timeouts": {
                "ready_ms": 8000,
                "send_ms": 5000,
                "response_ms": 120000,
                "stable_ms": 1500,
                "poll_interval_ms": 600,
            },
            "script": "script.oc",
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        template = f"""\
# {script_id} DSL script
# Selectors use: aria: placeholder: role: id: css: text:

LOAD {data.get('url', 'https://example.com')}

ON SEND
  WAIT_FOR     placeholder:Message
  TYPE         placeholder:Message  WITH $INPUT
  CLICK        aria:Send
  WAIT_WHILE   aria:Stop
END

ON READ
  EXTRACT      last role:assistant
END
"""
        with open(os.path.join(folder, "script.oc"), "w", encoding="utf-8") as f:
            f.write(template)
        return jsonify({"status": "ok", "id": script_id, "manifest": manifest})

    # ── Session management ─────────────────────────────────────────────────────

    @app.route("/mediator/sessions", methods=["GET"])
    def list_sessions():
        return jsonify({"sessions": session_manager.all_sessions()})

    @app.route("/mediator/start/<script_id>", methods=["POST"])
    def start_session(script_id: str):
        manifest = _load_manifest(script_id)
        if not manifest:
            return jsonify({"error": "Script not found"}), 404
        session_manager.get_or_create(
            script_id=script_id,
            url=manifest.get("url", ""),
            port=manifest.get("port", 0),
        )
        executor = _get_executor(script_id)
        if not executor:
            return jsonify({"error": "Failed to load executor"}), 500
        # Trigger LOAD in background so we don't block the HTTP response
        def _do_load():
            try:
                executor.load()
                session_manager.mark_loaded(script_id)
            except Exception as e:
                logger.error("load error for %s: %s", script_id, e)
        threading.Thread(target=_do_load, daemon=True).start()
        return jsonify({"status": "starting", "script_id": script_id})

    @app.route("/mediator/stop/<script_id>", methods=["POST"])
    def stop_session(script_id: str):
        _invalidate_executor(script_id)
        session_manager.invalidate(script_id)
        return jsonify({"status": "stopped"})

    # ── JS eval bridge (called by Android WebView) ─────────────────────────────

    @app.route("/mediator/eval/<script_id>", methods=["GET"])
    def poll_eval_request(script_id: str):
        """Android long-polls this to get the next JS to evaluate."""
        with _eval_req_lock:
            queue = _eval_requests.get(script_id, [])
            if queue:
                req = queue.pop(0)
                return jsonify({"pending": True, "id": req["id"], "js": req["js"]})
        return jsonify({"pending": False})

    @app.route("/mediator/eval/<script_id>", methods=["POST"])
    def post_eval_result(script_id: str):
        """Android posts the JS result back here."""
        data   = request.json or {}
        req_id = data.get("id")
        result = data.get("result")  # string or null

        with _eval_lock:
            evt = _eval_pending.get(req_id)
            if evt:
                _eval_results[req_id] = result
                evt.set()
        return jsonify({"status": "ok"})

    # ── OpenAI-compatible chat endpoint ────────────────────────────────────────

    @app.route("/v1/chat/completions/<script_id>", methods=["POST"])
    def mediator_chat(script_id: str):
        """
        OpenAI-compatible endpoint. Provider files point baseURL here.
        Example: baseURL = "http://localhost:5000/v1/chat/completions/chatgpt"
        Actually we expose /v1/chat/completions and detect script from model name
        OR use a dedicated port per script. For simplicity we use the script_id
        URL segment approach.
        """
        return _handle_chat(script_id, request)

    @app.route("/v1/chat/completions", methods=["POST"])
    def mediator_chat_default():
        """
        Fallback: detect script_id from the model field.
        Provider sets model = "chatgpt" or "gemini_web" etc.
        """
        data      = request.json or {}
        script_id = data.get("model", "")
        return _handle_chat(script_id, request)


def _handle_chat(script_id: str, req) -> Response:
    data     = req.json or {}
    messages = data.get("messages", [])
    tools    = data.get("tools")

    manifest = _load_manifest(script_id)
    if not manifest:
        return jsonify({"error": f"No script found for id: {script_id}"}), 404

    executor = _get_executor(script_id)
    if not executor:
        return jsonify({"error": "Executor load failed"}), 500

    # Auto-start the session on first request — no manual button needed.
    if not session_manager.is_loaded(script_id):
        session_manager.get_or_create(
            script_id=script_id,
            url=manifest.get("url", ""),
            port=manifest.get("port", 0),
        )
        load_done = threading.Event()
        def _do_load():
            try:
                executor.load()
                session_manager.mark_loaded(script_id)
            except Exception as e:
                logger.error("auto-load error for %s: %s", script_id, e)
            finally:
                load_done.set()
        threading.Thread(target=_do_load, daemon=True).start()
        ready_ms = manifest.get("timeouts", {}).get("ready_ms", 8000)
        if not load_done.wait(timeout=ready_ms / 1000):
            return jsonify({"error": "Timed out waiting for page to load"}), 504

    # Flatten conversation → single user string
    user_parts = []
    for m in messages:
        role    = m.get("role", "")
        content = m.get("content", "") or ""
        if role == "system":
            user_parts.append(f"[System]: {content}")
        elif role == "user":
            user_parts.append(content)
        elif role == "assistant":
            user_parts.append(f"[Assistant]: {content}")
    final_message = "\n".join(user_parts).strip()

    supports_tools = manifest.get("supports_tools", True)
    harness_active = bool(tools and not supports_tools)
    if harness_active:
        final_message = harness_wrap(final_message, tools)

    try:
        executor.send(final_message)
        reply_text = executor.read()
        session_manager.reset_errors(script_id)
    except DslError as e:
        needs_reauth = session_manager.record_error(script_id)
        error_msg = str(e)
        if needs_reauth:
            error_msg += " — too many errors, please re-authenticate in the browser."
        return jsonify({"error": error_msg}), 500
    except Exception as e:
        session_manager.record_error(script_id)
        return jsonify({"error": str(e)}), 500

    tool_calls   = None
    clean_reply  = reply_text

    if harness_active and reply_text:
        clean_reply, tool_calls = harness_parse(reply_text)

    response_body = {
        "id":      f"mediator-{uuid.uuid4().hex[:8]}",
        "object":  "chat.completion",
        "model":   script_id,
        "choices": [{
            "index":         0,
            "message": {
                "role":       "assistant",
                "content":    clean_reply or "",
                "tool_calls": tool_calls or None,
            },
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
    }
    return jsonify(response_body)
