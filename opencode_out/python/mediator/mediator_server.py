"""
mediator_server.py — OpenAI-compatible localhost endpoint for JS-scripted providers.

Registers these routes on the main Flask app:

  POST /mediator/start/<script_id>          Boot a script session
  POST /mediator/stop/<script_id>           Tear down a session
  GET  /mediator/sessions                   List active sessions
  GET  /mediator/scripts                    List available scripts in opencode/scripts/
  GET  /mediator/scripts/<script_id>        Read a script.js
  PUT  /mediator/scripts/<script_id>        Write/update a script.js
  POST /mediator/eval/<script_id>           Bridge: Android calls this to deliver JS eval results
  POST /v1/chat/completions/<script_id>     OpenAI-compat chat endpoint  (streaming via SSE)

Scripts are plain .js files; see scripts/demo/script.js for the template.
Set manifest "stream_selector" to enable token-streaming SSE responses.
"""

import json
import os
import uuid
import threading
import time

try:
    import gevent.event
    import gevent.lock
    _Event = gevent.event.Event
    _Lock  = gevent.lock.RLock
except ImportError:
    _Event = threading.Event
    _Lock  = threading.Lock

import logging
from typing import Optional

from flask import Flask, request, jsonify, Response, stream_with_context

from .js_executor import JsExecutor, JsError
from .tool_harness import wrap as harness_wrap, parse as harness_parse
from .script_session import session_manager

logger = logging.getLogger(__name__)

# ── JS eval bridge ─────────────────────────────────────────────────────────────
# Android WebView posts results here; Python side blocks waiting for them.
_eval_pending: dict[str, threading.Event]   = {}
_eval_results: dict[str, Optional[str]]     = {}
_eval_lock = _Lock()

_eval_requests: dict[str, list[dict]] = {}
_eval_req_lock = _Lock()


def _build_evaluator(script_id: str, timeout_sec: float = 10.0):
    """Returns an evaluator wired to the Android bridge (unchanged from before)."""
    def evaluator(js: str) -> Optional[str]:
        req_id = str(uuid.uuid4())
        evt    = _Event()

        with _eval_lock:
            _eval_pending[req_id] = evt
            _eval_results[req_id] = None

        with _eval_req_lock:
            _eval_requests.setdefault(script_id, []).append({"id": req_id, "js": js})

        logger.debug("[eval] enqueued %s for %s: %.60s", req_id[:8], script_id, js)
        got = evt.wait(timeout=timeout_sec)

        with _eval_lock:
            result = _eval_results.pop(req_id, None)
            _eval_pending.pop(req_id, None)

        if not got:
            raise RuntimeError(
                f"Android bridge timeout ({timeout_sec:.0f}s) for '{script_id}'. "
                f"MediatorBridgePoller may not be running."
            )

        logger.debug("[eval] result %s: %.60s", req_id[:8], str(result))
        return result

    return evaluator


# ── Executor cache ─────────────────────────────────────────────────────────────
_executors: dict[str, JsExecutor] = {}
_exec_lock = _Lock()


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


def _get_executor(script_id: str) -> Optional[JsExecutor]:
    with _exec_lock:
        if script_id in _executors:
            return _executors[script_id]

    manifest = _load_manifest(script_id)
    if not manifest:
        return None

    script_name = manifest.get("script", "script.js")
    script_path = os.path.join(_get_scripts_dir(), script_id, script_name)
    if not os.path.isfile(script_path):
        return None

    timeout_sec = manifest.get("timeouts", {}).get("eval_bridge_ms", 10_000) / 1000
    evaluator   = _build_evaluator(script_id, timeout_sec=timeout_sec)
    executor    = JsExecutor(script_path, manifest, evaluator)

    with _exec_lock:
        _executors[script_id] = executor
    return executor


def _invalidate_executor(script_id: str):
    with _exec_lock:
        _executors.pop(script_id, None)


# ── Seed default scripts ───────────────────────────────────────────────────────

def seed_default_scripts():
    """Seed bundled scripts/ into opencode/scripts/ on first install only.
    Skips any file that already exists — never overwrites user edits.
    Never deletes files the user added themselves.
    """
    import shutil, sys
    scripts_dir = _get_scripts_dir()

    bundle_dir = None
    for root in sys.path:
        candidate = os.path.join(root, "scripts")
        if os.path.isdir(candidate):
            bundle_dir = candidate
            break

    if not bundle_dir:
        return

    for name in os.listdir(bundle_dir):
        src = os.path.join(bundle_dir, name)
        dst = os.path.join(scripts_dir, name)
        if not os.path.isdir(src):
            continue
        os.makedirs(dst, exist_ok=True)
        # Only copy if the file doesn't exist yet — never overwrite user edits.
        for fname in os.listdir(src):
            dst_file = os.path.join(dst, fname)
            if not os.path.exists(dst_file):
                shutil.copy2(os.path.join(src, fname), dst_file)


# ── Route registration ─────────────────────────────────────────────────────────

def register_mediator_routes(app: Flask):

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
            manifest    = _load_manifest(name) or {}
            script_file = manifest.get("script", "script.js")
            script_path = os.path.join(folder, script_file)
            results.append({
                "id":              name,
                "label":           manifest.get("label", name),
                "url":             manifest.get("url", ""),
                "port":            manifest.get("port", 0),
                "supports_tools":  manifest.get("supports_tools", False),
                "stream_selector": manifest.get("stream_selector", ""),
                "script_file":     script_file,
                "has_script":      os.path.isfile(script_path),
                "folder":          folder,
            })
        return jsonify({"scripts": results, "scripts_dir": scripts_dir})

    @app.route("/mediator/scripts/<script_id>", methods=["GET"])
    def get_script(script_id: str):
        manifest = _load_manifest(script_id)
        if not manifest:
            return jsonify({"error": "Script not found"}), 404
        script_file = manifest.get("script", "script.js")
        script_path = os.path.join(_get_scripts_dir(), script_id, script_file)
        content = ""
        if os.path.isfile(script_path):
            with open(script_path, "r", encoding="utf-8") as f:
                content = f.read()
        return jsonify({"id": script_id, "manifest": manifest, "content": content})

    @app.route("/mediator/scripts/<script_id>", methods=["PUT"])
    def update_script(script_id: str):
        data        = request.json or {}
        scripts_dir = _get_scripts_dir()
        folder      = os.path.join(scripts_dir, script_id)
        os.makedirs(folder, exist_ok=True)

        if "manifest" in data:
            with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(data["manifest"], f, indent=2)

        if "content" in data:
            manifest    = _load_manifest(script_id) or {}
            script_file = manifest.get("script", "script.js")
            with open(os.path.join(folder, script_file), "w", encoding="utf-8") as f:
                f.write(data["content"])

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
        data        = request.json or {}
        scripts_dir = _get_scripts_dir()
        folder      = os.path.join(scripts_dir, script_id)
        if os.path.isdir(folder):
            return jsonify({"error": "Already exists"}), 409
        os.makedirs(folder, exist_ok=True)

        manifest = {
            "id":              script_id,
            "label":           data.get("label", script_id),
            "url":             data.get("url", ""),
            "port":            data.get("port", 11440),
            "supports_tools":  False,
            "stream_selector": "",
            "timeouts": {
                "ready_ms":        20000,
                "send_ms":          5000,
                "response_ms":    120000,
                "stable_ms":        1500,
                "poll_interval_ms":  300,
                "eval_bridge_ms":  10000,
            },
            "script": "script.js",
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        target_url = data.get("url", "https://example.com")
        template = f"""\
// {script_id} — JavaScript provider script
// $oc helpers: waitFor, waitWhile, waitNew, waitStable,
//              click, type, clear, pressKey, navigate,
//              extractLast, extractFirst, extractUrl, sleep

const LOAD_URL = "{target_url}";

async function onLoad() {{
  await $oc.waitFor("placeholder:Message");
}}

async function onSend(input) {{
  await $oc.waitFor("placeholder:Message");
  await $oc.type("placeholder:Message", input);
  await $oc.click("aria:Send");
  // For streaming providers: stop here (waitNew for response element).
  // For non-streaming: add  await $oc.waitWhile("aria:Stop");
  await $oc.waitNew("role:assistant");
}}

async function onRead() {{
  // Called only for non-streaming providers (no stream_selector in manifest).
  await $oc.waitStable("role:assistant", 1500);
  return $oc.extractLast("role:assistant");
}}
"""
        with open(os.path.join(folder, "script.js"), "w", encoding="utf-8") as f:
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

        def _do_load():
            try:
                executor.load()
                session_manager.mark_loaded(script_id)
            except Exception as e:
                logger.error("load error for %s: %s", script_id, e)

        try:
            import gevent
            gevent.spawn(_do_load)
        except ImportError:
            threading.Thread(target=_do_load, daemon=True).start()

        return jsonify({"status": "starting", "script_id": script_id})

    @app.route("/mediator/stop/<script_id>", methods=["POST"])
    def stop_session(script_id: str):
        _invalidate_executor(script_id)
        session_manager.invalidate(script_id)
        return jsonify({"status": "stopped"})

    # ── JS eval bridge (Android WebView) ───────────────────────────────────────

    @app.route("/mediator/eval/<script_id>", methods=["GET"])
    def poll_eval_request(script_id: str):
        """Android long-polls — holds connection up to 10s waiting for a job."""
        deadline = time.time() + 10.0
        while time.time() < deadline:
            with _eval_req_lock:
                queue = _eval_requests.get(script_id, [])
                if queue:
                    req = queue.pop(0)
                    logger.debug("[poll] delivering %s to Android for %s",
                                 req["id"][:8], script_id)
                    return jsonify({"pending": True, "id": req["id"], "js": req["js"]})
            time.sleep(0.02)
        return jsonify({"pending": False})

    @app.route("/mediator/eval/<script_id>", methods=["POST"])
    def post_eval_result(script_id: str):
        """Android posts the JS result back here."""
        data   = request.json or {}
        req_id = data.get("id")
        result = data.get("result")

        with _eval_lock:
            evt = _eval_pending.get(req_id)
            if evt:
                _eval_results[req_id] = result
                evt.set()
        return jsonify({"status": "ok"})

    # ── Debug endpoint ─────────────────────────────────────────────────────────

    @app.route("/mediator/debug/<script_id>", methods=["GET"])
    def debug_script(script_id: str):
        executor = _get_executor(script_id)
        if not executor:
            return jsonify({"error": f"No executor for '{script_id}'"}), 404

        def quick_eval(js):
            try:    return executor._eval_raw(js)
            except: return "ERROR"

        ping = quick_eval("'pong'")
        result = {
            "script_id":          script_id,
            "session_loaded":     session_manager.is_loaded(script_id),
            "pending_evals":      len(_eval_requests.get(script_id, [])),
            "bridge_alive":       ping == "pong",
            "bridge_ping":        ping,
            "oc_loaded":          quick_eval("window.__ocLoaded === true ? 'yes' : 'no'"),
        }
        if result["bridge_alive"]:
            result["webview_url"] = quick_eval("window.location.href")
            result["page_title"]  = quick_eval("document.title")
        return jsonify(result)

    # ── OpenAI-compatible chat endpoint ────────────────────────────────────────

    @app.route("/v1/chat/completions/<script_id>", methods=["POST"])
    def mediator_chat(script_id: str):
        return _handle_chat(script_id, request)

    @app.route("/v1/chat/completions", methods=["POST"])
    def mediator_chat_default():
        data      = request.json or {}
        script_id = data.get("model", "")
        return _handle_chat(script_id, request)


# ── Chat handler ───────────────────────────────────────────────────────────────

def _handle_chat(script_id: str, req) -> Response:
    data     = req.json or {}
    messages = data.get("messages", [])
    tools    = data.get("tools")
    want_stream = data.get("stream", False)

    manifest = _load_manifest(script_id)
    if not manifest:
        return jsonify({"error": f"No script found for id: {script_id}"}), 404

    executor = _get_executor(script_id)
    if not executor:
        return jsonify({"error": "Executor load failed"}), 500

    # Auto-start session on first request.
    if not session_manager.is_loaded(script_id):
        session_manager.get_or_create(
            script_id=script_id,
            url=manifest.get("url", ""),
            port=manifest.get("port", 0),
        )
        load_done  = _Event()
        load_error = [None]

        def _do_load():
            try:
                executor.load()
                session_manager.mark_loaded(script_id)
            except Exception as e:
                load_error[0] = str(e)
            finally:
                load_done.set()

        try:
            import gevent
            gevent.spawn(_do_load)
        except ImportError:
            threading.Thread(target=_do_load, daemon=True).start()

        load_timeout = manifest.get("timeouts", {}).get("response_ms", 30000) / 1000 + 5
        if not load_done.wait(timeout=load_timeout):
            return jsonify({"id": f"mediator-err-{uuid.uuid4().hex[:8]}", "object": "chat.completion", "model": script_id, "choices": [{"index": 0, "message": {"role": "assistant", "content": "[Load timed out] Bridge did not respond"}, "finish_reason": "stop"}]})
        if load_error[0]:
            return jsonify({"id": f"mediator-err-{uuid.uuid4().hex[:8]}", "object": "chat.completion", "model": script_id, "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[Load failed] {load_error[0]}"}, "finish_reason": "stop"}]})

    # Build the input string from messages.
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

    stream_selector = manifest.get("stream_selector", "")
    stable_ms       = manifest.get("timeouts", {}).get("stream_stable_ms",
                       manifest.get("timeouts", {}).get("stable_ms", 1500))

    # ── Streaming SSE response ────────────────────────────────────────────────
    if want_stream and stream_selector:
        # Read thinking_snapshot_js from manifest (set by chatgpt script).
        thinking_js = manifest.get("thinking_snapshot_js", None)

        def _generate():
            resp_id = f"mediator-{uuid.uuid4().hex[:8]}"

            try:
                executor.send(final_message)
                session_manager.reset_errors(script_id)
            except JsError as e:
                session_manager.record_error(script_id)
                err_event = {"error": {"message": str(e), "type": "js_error"}}
                yield f"data: {json.dumps(err_event)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except Exception as e:
                session_manager.record_error(script_id)
                err_event = {"error": {"message": str(e), "type": "internal_error"}}
                yield f"data: {json.dumps(err_event)}\n\n"
                yield "data: [DONE]\n\n"
                return

            full_text = ""
            try:
                # Use stream_read_with_thinking when the manifest declares a
                # thinking_snapshot_js; this surfaces reasoning tokens as
                # delta chunks with finish_reason="thinking" (non-standard but
                # recognizable by the OpenCode UI).  Falls back gracefully to
                # plain text chunks when the JS helper is absent.
                for item in executor.stream_read_with_thinking(
                    stream_selector,
                    thinking_js=thinking_js,
                    stable_ms=stable_ms,
                ):
                    chunk_type = item.get("type", "text")
                    chunk_text = item.get("text", "")

                    if chunk_type == "thinking":
                        # Emit as a special reasoning delta so the UI can
                        # display it in a collapsible "Thinking…" block.
                        event = {
                            "id":      resp_id,
                            "object":  "chat.completion.chunk",
                            "model":   script_id,
                            "choices": [{
                                "index":         0,
                                "delta":         {"role": "assistant", "reasoning_content": chunk_text},
                                "finish_reason": None,
                            }],
                        }
                    else:
                        full_text += chunk_text
                        event = {
                            "id":      resp_id,
                            "object":  "chat.completion.chunk",
                            "model":   script_id,
                            "choices": [{
                                "index":         0,
                                "delta":         {"role": "assistant", "content": chunk_text},
                                "finish_reason": None,
                            }],
                        }
                    yield f"data: {json.dumps(event)}\n\n"

            except JsError as e:
                err_event = {"error": {"message": str(e), "type": "js_error"}}
                yield f"data: {json.dumps(err_event)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Handle tool calls in the full text (harness mode).
            if harness_active and full_text:
                clean, tool_calls = harness_parse(full_text)
                if tool_calls:
                    for i, tc in enumerate(tool_calls):
                        tool_event = {
                            "id":      resp_id,
                            "object":  "chat.completion.chunk",
                            "model":   script_id,
                            "choices": [{
                                "index": 0,
                                "delta": {"tool_calls": [tc]},
                                "finish_reason": None,
                            }],
                        }
                        yield f"data: {json.dumps(tool_event)}\n\n"

            # Final stop chunk.
            stop_event = {
                "id":      resp_id,
                "object":  "chat.completion.chunk",
                "model":   script_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(stop_event)}\n\n"
            yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(_generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control":    "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Non-streaming response ────────────────────────────────────────────────
    try:
        executor.send(final_message)
        reply_text = executor.read()
        session_manager.reset_errors(script_id)
    except JsError as e:
        needs_reauth = session_manager.record_error(script_id)
        error_msg = f"[Script error] {e}"
        if needs_reauth:
            error_msg += " — too many errors, please re-authenticate in the browser."
        return jsonify({
            "id": f"mediator-err-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "model": script_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": error_msg}, "finish_reason": "stop"}],
        })
    except Exception as e:
        session_manager.record_error(script_id)
        return jsonify({
            "id": f"mediator-err-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "model": script_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[Internal error] {e}"}, "finish_reason": "stop"}],
        })

    tool_calls  = None
    clean_reply = reply_text

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
