"""
Flask route definitions.

All routes are registered on the `app` Flask instance imported from app.py.
Keeping routes here avoids the 2000-line monolith while letting app.py
remain a clean entry point.
"""

import os
import json
import threading
import queue as _queue_mod
from datetime import datetime

import requests
from flask import request, jsonify, send_file, send_from_directory, Response, stream_with_context

from python.config import COMPACTION_API_URL, COMPACTION_MODEL, MAX_TOKENS
from python.providers import get_provider, all_models, get_model_ctx, compaction_buffer, get_reasoning_effort, needs_reasoning_passback
import python.state as state
import python.agents as agents_mod
from python.storage import get_librecode_dir, chats_index_file, chat_file, resolve_path, is_within_dir
from python.tools import get_tools_for_agent, reload_agents as _reload_agents, run_tool
from python.subagent import run_subagent_streaming, _subagent_semaphore
from python.compaction import (
    compact_messages,
    build_compacted_messages_for_api,
    split_head_tail,
    generate_summary,
)

# Lazily resolved by init_app()
_flask_app = None
ROOT       = None


def init_app(app, root: str) -> None:
    """Call once from app.py to bind the Flask instance and register routes."""
    global _flask_app, ROOT
    _flask_app = app
    ROOT       = root
    _register(app)
    # Mediator scripting engine routes
    from python.mediator import register_mediator_routes
    register_mediator_routes(app)


# ── ID generation ──────────────────────────────────────────────────────

def _next_id(chat_id: str, prefix: str) -> str:
    key = f"__seq_{chat_id}"
    n   = state.chat_msg_counts.get(key, 0) + 1
    state.chat_msg_counts[key] = n
    return f"{prefix}_{n}"


# ── History conversion ──────────────────────────────────────────────────

def history_to_api_messages(history: list, model_id: str = "") -> list:
    _passback = needs_reasoning_passback(model_id) if model_id else False
    out = []
    for turn in history:
        role = turn.get("role")
        if role == "user":
            out.append({"role": "user", "content": turn.get("content", "")})
        elif role == "assistant":
            tcs = turn.get("tool_calls")
            text = turn.get("content") or ""
            msg = {"role": "assistant", "content": text}
            if tcs:
                msg["tool_calls"] = tcs
            # DeepSeek V4 (and any future model with NEEDS_REASONING_PASSBACK)
            # requires reasoning_content to be echoed back in every assistant turn
            if _passback:
                rc = turn.get("reasoning_content")
                msg["reasoning_content"] = rc if rc is not None else ""
            out.append(msg)
        elif role == "tool":
            out.append({
                "role":         "tool",
                "tool_call_id": turn.get("tool_call_id", ""),
                "content":      turn.get("content", ""),
            })
    return out


# ── Route registration ─────────────────────────────────────────────────

def _register(app) -> None:

    # ── Static ──────────────────────────────────────────────────────────

    @app.route("/")
    def home():
        return send_file(os.path.join(ROOT, "index.html"))

    @app.route("/ui/<path:filename>")
    def static_files(filename):
        return send_from_directory(os.path.join(ROOT, "ui"), filename)

    # ── Misc / health ────────────────────────────────────────────────────

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"status": "ok"})

    @app.route("/storage_dir", methods=["GET"])
    def storage_dir():
        return jsonify({"path": get_librecode_dir()})

    # ── Working directory ────────────────────────────────────────────────

    @app.route("/working_dir", methods=["GET"])
    def get_working_dir():
        return jsonify({"working_dir": state.working_dir})

    @app.route("/working_dir", methods=["POST"])
    def set_working_dir():
        data    = request.json
        new_dir = data.get("working_dir", "")
        if new_dir and os.path.isdir(new_dir):
            state.working_dir = new_dir
            return jsonify({"status": "ok", "working_dir": state.working_dir})
        elif new_dir:
            return jsonify({"status": "error", "message": "Invalid directory"})
        else:
            state.working_dir = ""
            return jsonify({"status": "ok", "working_dir": ""})

    @app.route("/working_dirs", methods=["POST"])
    def set_working_dirs():
        data    = request.json
        dirs    = data.get("working_dirs", [])
        valid   = [d for d in dirs if d and os.path.isdir(d)]
        invalid = [d for d in dirs if d and not os.path.isdir(d)]
        state.working_dirs = valid
        state.working_dir  = valid[0] if valid else ""
        return jsonify({"status": "ok", "working_dirs": state.working_dirs, "invalid_dirs": invalid})

    # ── Directory listing ────────────────────────────────────────────────

    @app.route("/ls", methods=["GET"])
    def list_dir():
        path = request.args.get("path", state.working_dir)
        if not state.working_dir:
            return jsonify({"error": "No working directory set"})
        full_path = resolve_path(path) if path else state.working_dir
        if not is_within_dir(full_path, state.working_dir):
            return jsonify({"error": "Path outside working directory"})
        if not os.path.isdir(full_path):
            return jsonify({"error": f"Not a directory: {path}"})
        if not os.access(full_path, os.R_OK):
            return jsonify({"error": f"Permission denied: {path}", "permission_error": True})
        try:
            items = []
            for name in os.listdir(full_path):
                fpath = os.path.join(full_path, name)
                items.append({
                    "name":   name,
                    "is_dir": os.path.isdir(fpath),
                    "path":   os.path.relpath(fpath, state.working_dir),
                })
            items.sort(key=lambda x: (not x["is_dir"], x["name"]))
            return jsonify({"items": items, "cwd": state.working_dir})
        except Exception as e:
            return jsonify({"error": str(e)})

    # ── Chat management ──────────────────────────────────────────────────

    @app.route("/switch_chat", methods=["POST"])
    def switch_chat():
        data    = request.json
        chat_id = data.get("chat_id")
        state.current_chat_id = chat_id
        if chat_id:
            raw_history = data.get("history", [])
            cleaned = []
            for t in raw_history:
                if t.get("_pending"):
                    continue  # drop unsent user messages
                if t.get("_partial"):
                    if not t.get("content"):
                        continue
                    t = {k: v for k, v in t.items() if k != "_partial"}
                    if not t["content"].endswith("[interrupted]"):
                        t["content"] = t["content"] + " [interrupted]"
                cleaned.append(t)
            state.chat_histories[chat_id] = cleaned
            # display history is loaded from JS (the full chat.history) —
            # if JS sends a separate display_history key use it, otherwise mirror api history
            display = data.get("display_history", cleaned)
            state.chat_display_histories[chat_id] = [
                t for t in display if not t.get("_pending")
            ]
            if "summary" in data:
                state.chat_summaries[chat_id] = data["summary"]
            max_seq = 0
            for t in state.chat_histories[chat_id]:
                tid   = t.get("id", "")
                parts = tid.split("_")
                if len(parts) == 2 and parts[1].isdigit():
                    max_seq = max(max_seq, int(parts[1]))
            seq_key = f"__seq_{chat_id}"
            state.chat_msg_counts[seq_key] = max(state.chat_msg_counts.get(seq_key, 0), max_seq)
        return jsonify({"status": "ok"})

    @app.route("/clear", methods=["POST"])
    def clear():
        data    = request.json or {}
        chat_id = data.get("chat_id", "default")
        state.chat_histories.pop(chat_id, None)
        state.chat_summaries.pop(chat_id, None)
        state.chat_token_counts.pop(chat_id, None)
        return jsonify({"status": "cleared"})

    @app.route("/delete_chat", methods=["POST"])
    def delete_chat():
        data = request.json
        cid  = data.get("chat_id", "")
        if not cid:
            return jsonify({"status": "error", "message": "No chat_id"})
        state.chat_histories.pop(cid, None)
        state.chat_summaries.pop(cid, None)
        state.chat_token_counts.pop(cid, None)
        try:
            path = chat_file(cid)
            if os.path.isfile(path):
                os.remove(path)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    # ── Chat persistence ─────────────────────────────────────────────────

    @app.route("/save_chats", methods=["POST"])
    def save_chats():
        data      = request.json
        chats     = data.get("chats", [])
        active_id = data.get("activeChatId")
        try:
            for chat in chats:
                cid = chat.get("id", "")
                if not cid:
                    continue
                with open(chat_file(cid), "w", encoding="utf-8") as f:
                    json.dump(chat, f, ensure_ascii=False, indent=2)

            incoming_ids = [c["id"] for c in chats if c.get("id")]
            incoming_set = set(incoming_ids)

            # ── Guard against race-condition overwrites ───────────────────
            # On reinstall storage_dir.txt may not exist yet when the first
            # loadChats fires, so the JS sees an empty list and immediately
            # calls saveChats with one freshly-created chat.  Without this
            # guard that would silently wipe index.json.
            #
            # Strategy: collect every chat ID that is genuinely on disk right
            # now, then merge it with the incoming list.  IDs that appear on
            # disk but NOT in the incoming list are preserved in the index
            # (they were real chats the JS never loaded due to the race).
            # IDs that were explicitly deleted by the user will have had their
            # .json files removed first, so they won't show up in the scan.

            lib_dir = get_librecode_dir()

            # 1. IDs the existing index already knows about
            existing_ids: list = []
            try:
                with open(chats_index_file(), "r", encoding="utf-8") as f:
                    existing_ids = json.load(f).get("chatIds", [])
            except Exception:
                pass

            # 2. IDs with actual .json files on disk
            disk_ids: set = set()
            try:
                for fn in os.listdir(lib_dir):
                    if fn.endswith(".json") and fn not in ("index.json",):
                        disk_ids.add(fn[:-5])
            except Exception:
                pass

            # 3. Merge: keep incoming order, then append any survivor IDs
            #    that exist on disk but were absent from this save payload.
            survivor_ids = [
                cid for cid in existing_ids
                if cid not in incoming_set and cid in disk_ids
            ]
            merged_ids = incoming_ids + survivor_ids

            index = {
                "activeChatId": active_id,
                "chatIds":      merged_ids,
            }
            with open(chats_index_file(), "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    @app.route("/load_chats", methods=["GET"])
    def load_chats():
        try:
            # ── 1. Read index (tolerate missing / corrupt file) ───────────
            index = {}
            try:
                with open(chats_index_file(), "r", encoding="utf-8") as f:
                    index = json.load(f)
            except FileNotFoundError:
                pass
            except Exception:
                pass  # corrupt index — we'll recover below

            chat_ids = list(index.get("chatIds", []))

            # ── 2. If index is empty, scan dir for existing chat JSON files ─
            if not chat_ids:
                lib_dir = get_librecode_dir()
                try:
                    for fn in sorted(os.listdir(lib_dir)):
                        if fn.endswith(".json") and fn != "index.json":
                            chat_ids.append(fn[:-5])  # strip .json to get chat_id
                except Exception:
                    pass

            # ── 3. Load each chat file ────────────────────────────────────
            chats      = []
            loaded_ids = set()
            for cid in chat_ids:
                try:
                    with open(chat_file(cid), "r", encoding="utf-8") as f:
                        chats.append(json.load(f))
                    loaded_ids.add(cid)
                except Exception:
                    state.chat_histories.pop(cid, None)
                    state.chat_summaries.pop(cid, None)

            # ── 4. Evict server-side state for chats no longer on disk ────
            for cid in list(state.chat_histories.keys()):
                if cid not in loaded_ids:
                    state.chat_histories.pop(cid, None)
                    state.chat_summaries.pop(cid, None)

            active = index.get("activeChatId")
            if active not in loaded_ids:
                active = chats[0]["id"] if chats else None
            return jsonify({"chats": chats, "activeChatId": active})
        except Exception as e:
            return jsonify({"chats": [], "activeChatId": None, "error": str(e)})

    # ── Compaction ───────────────────────────────────────────────────────

    @app.route("/compact", methods=["POST"])
    def manual_compact():
        data    = request.json or {}
        chat_id = data.get("chat_id", "default")
        model   = data.get("model", COMPACTION_MODEL)

        if chat_id not in state.chat_histories or not state.chat_histories[chat_id]:
            return jsonify({"status": "ok", "compacted": False, "history": []})

        history          = state.chat_histories.get(chat_id, [])
        flat             = history_to_api_messages(history, model)
        previous_summary = state.chat_summaries.get(chat_id)

        # Use the real model context window (not the compaction threshold constant).
        # This is a FORCED compact: skip the overflow guard — user explicitly asked
        # for it, so compact whatever we have regardless of current token count.
        ctx = get_model_ctx(model)

        # If there's only a single user/assistant exchange there's genuinely nothing
        # to split.  Summarise the whole history as head with empty tail.
        head, tail = split_head_tail(flat, ctx, MAX_TOKENS)
        if not head:
            # Fewer than 2 turns — summarise entire history
            head = flat
            tail = []

        summary = generate_summary(COMPACTION_API_URL, model, head, previous_summary)
        if not summary:
            return jsonify({"status": "error", "message": "Summary generation failed", "history": history})

        state.chat_summaries[chat_id] = summary
        compaction_marker = {
            "role":        "user",
            "content":     f"[Context compacted]\n\n{summary}",
            "_compaction": True,
        }
        compacted_flat = [compaction_marker] + tail
        # Only replace the API-facing history — display history stays intact
        state.chat_histories[chat_id] = build_compacted_messages_for_api(compacted_flat)

        return jsonify({
            "status":      "ok",
            "compacted":   True,
            "history":     state.chat_display_histories.get(chat_id, state.chat_histories[chat_id]),
            "api_history": state.chat_histories[chat_id],
            "summary":     summary,
        })

    # ── Token counts ─────────────────────────────────────────────────────

    @app.route("/token_counts", methods=["GET"])
    def token_counts_route():
        chat_id = request.args.get("chat_id", "default")
        counts  = state.chat_token_counts.get(chat_id, {"input": 0, "output": 0})
        return jsonify({
            "chat_id": chat_id,
            "input":   counts["input"],
            "output":  counts["output"],
            "total":   counts["input"] + counts["output"],
        })

    @app.route("/token_counts", methods=["DELETE"])
    def reset_token_counts():
        chat_id = request.args.get("chat_id", "default")
        state.chat_token_counts.pop(chat_id, None)
        return jsonify({"status": "ok"})

    # ── Agents ───────────────────────────────────────────────────────────

    @app.route("/agents", methods=["GET"])
    def list_agents():
        agents_list = [
            {
                "id":          agent_id,
                "name":        profile.get("name", agent_id),
                "description": profile.get("description", ""),
            }
            for agent_id, profile in agents_mod.AGENT_PROFILES.items()
        ]
        return jsonify({"agents": agents_list})

    @app.route("/prompts_dir", methods=["GET"])
    def prompts_dir_route():
        from python.agents import get_prompts_dir
        return jsonify({"path": get_prompts_dir()})

    @app.route("/reload_agents", methods=["POST"])
    def reload_agents_route():
        _reload_agents()
        return jsonify({"status": "ok", "agents": list(agents_mod.AGENT_PROFILES.keys())})

    # ── Main chat endpoint ───────────────────────────────────────────────

    @app.route("/models", methods=["GET"])
    def list_models():
        return jsonify(all_models())

    @app.route("/chat", methods=["POST"])
    def chat():
        data       = request.json
        user_msg   = data.get("message", "")
        model      = data.get("model", COMPACTION_MODEL)
        agent_name = data.get("agent", "build")
        chat_id    = data.get("chat_id", "default")

        if chat_id not in state.chat_histories:
            state.chat_histories[chat_id] = []

        history = state.chat_histories[chat_id]

        last = history[-1] if history else None
        if not (last and last.get("role") == "user" and last.get("content") == user_msg):
            history.append({"id": _next_id(chat_id, "u"), "role": "user", "content": user_msg})

        dirs = state.working_dirs if state.working_dirs else ([state.working_dir] if state.working_dir else [])
        if agent_name not in agents_mod.AGENT_PROFILES:
            agent_name = next(iter(agents_mod.AGENT_PROFILES), "build")
        agent_profile = agents_mod.AGENT_PROFILES.get(agent_name, {})
        agent_suffix  = agent_profile.get("system_suffix", "")
        active_tools  = get_tools_for_agent(agent_name)

        now_str       = datetime.now().strftime("%A, %B %d, %Y %H:%M")
        base_prompt   = (agents_mod.SYSTEM_PROMPT_BASE + "\n\n") if agents_mod.SYSTEM_PROMPT_BASE else ""
        datetime_line = f"Current date/time: {now_str}\n\n"

        if dirs:
            dir_list = "\n".join(f"- {d}" for d in dirs)
            system_msg = {
                "role":    "system",
                "content": (
                    f"{base_prompt}{datetime_line}Working director{'y' if len(dirs) == 1 else 'ies'}:\n{dir_list}\n\n"
                    f"Use glob, fd, or rg to explore. Never navigate above these paths.\n\n---\n{agent_suffix}"
                ),
            }
        else:
            system_msg = {
                "role":    "system",
                "content": f"{base_prompt}{datetime_line}No working directory set. Ask the user to select a project folder first.\n\n---\n{agent_suffix}",
            }

        def generate():
            import time
            full_content   = ""
            full_reasoning = ""
            last_heartbeat = time.time()
            state.current_chat_id = chat_id

            # Per-chat token tracking — init if first message in this chat
            if chat_id not in state.chat_token_counts:
                state.chat_token_counts[chat_id] = {"input": 0, "output": 0}
            token_counts = state.chat_token_counts[chat_id]

            try:
                provider = get_provider(model)
            except ValueError:
                yield f"data: {json.dumps({'type': 'error', 'text': f'No provider for model: {model}'})}\n\n"
                return

            previous_summary = state.chat_summaries.get(chat_id)
            flat_history     = history_to_api_messages(list(history), model)

            # Real context window + variable buffer (OpenCode style)
            ctx    = get_model_ctx(model)
            buffer = compaction_buffer(ctx)

            compacted, new_summary, did_compact = compact_messages(
                messages         = flat_history,
                system_messages  = [system_msg],
                api_url          = COMPACTION_API_URL,
                model            = COMPACTION_MODEL,
                previous_summary = previous_summary,
                context_limit    = ctx,
                max_output_tokens = min(MAX_TOKENS, buffer),
            )
            if did_compact:
                state.chat_summaries[chat_id] = new_summary
                yield f"data: {json.dumps({'type': 'compaction', 'text': 'Context compacted.', 'summary': new_summary, 'api_history': build_compacted_messages_for_api(compacted)})}\n\n"

            messages       = [system_msg] + build_compacted_messages_for_api(compacted)
            new_rich_turns = []

            from python.compaction import estimate_messages_tokens as _est
            _got_real_usage = False

            for _round in range(1000):
                tool_calls_acc  = {}
                round_content   = ""
                round_reasoning = ""
                round_blocks    = []
                _last_block_type = None

                try:
                    events = provider.stream_chat(model, messages, active_tools, MAX_TOKENS, get_reasoning_effort(model))
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
                    return

                for event in events:
                    evtype = event.get("type")

                    if evtype == "text":
                        text = event["text"]
                        round_content  += text
                        full_content   += text
                        if _last_block_type != "text":
                            round_blocks.append({"type": "text", "content": text})
                            _last_block_type = "text"
                        else:
                            round_blocks[-1]["content"] += text
                        yield f"data: {json.dumps({'type': 'text', 'text': text})}\n\n"

                    elif evtype == "thinking":
                        text = event["text"]
                        round_reasoning += text
                        full_reasoning  += text
                        if _last_block_type != "thinking":
                            round_blocks.append({"type": "thinking", "content": text})
                            _last_block_type = "thinking"
                        else:
                            round_blocks[-1]["content"] += text
                        yield f"data: {json.dumps({'type': 'thinking', 'text': text})}\n\n"

                    elif evtype == "tool_delta":
                        idx = event.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        tc = tool_calls_acc[idx]
                        if event.get("id"):
                            tc["id"] = event["id"]
                        if event.get("name"):
                            tc["name"] += event["name"]
                        if event.get("arguments"):
                            tc["arguments"] += event["arguments"]

                    elif evtype == "usage":
                        token_counts["input"]  += event.get("input_tokens", 0)
                        token_counts["output"] += event.get("output_tokens", 0)
                        _got_real_usage = True

                    elif evtype == "done":
                        pass  # handled below

                    elif evtype == "error":
                        yield f"data: {json.dumps({'type': 'error', 'text': event['text']})}\n\n"
                        return

                    if time.time() - last_heartbeat > 8:
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                        last_heartbeat = time.time()

                if not tool_calls_acc:
                    rich_asst = {
                        "id":               _next_id(chat_id, "a"),
                        "role":             "assistant",
                        "content":          full_content,
                        "reasoning_content": full_reasoning or None,
                        "content_blocks":   round_blocks,
                        "tool_calls":       [],
                    }
                    new_rich_turns.append(rich_asst)
                    if not _got_real_usage:
                        token_counts["input"]  += _est(messages)
                        token_counts["output"] += _est(full_content) + _est(full_reasoning)
                    yield f"data: {json.dumps({'type': 'token_update', 'input': token_counts['input'], 'output': token_counts['output'], 'total': token_counts['input'] + token_counts['output']})}\n\n"
                    break

                tc_list = []
                for idx in sorted(tool_calls_acc.keys()):
                    tc    = tool_calls_acc[idx]
                    tc_id = tc["id"] or _next_id(chat_id, f"tc{idx}")
                    tc_list.append({
                        "id":   tc_id,
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    })

                rich_asst = {
                    "id":               _next_id(chat_id, "a"),
                    "role":             "assistant",
                    "content":          round_content or "",
                    "reasoning_content": round_reasoning or None,
                    "content_blocks":   round_blocks,
                    "tool_calls":       tc_list,
                }
                new_rich_turns.append(rich_asst)

                flat_asst = {"role": "assistant", "content": round_content or "", "tool_calls": tc_list}
                if needs_reasoning_passback(model):
                    flat_asst["reasoning_content"] = round_reasoning or ""
                messages.append(flat_asst)

                # ── Parallel tool execution ────────────────────────────────
                _TOOL_RESULT_MAX = 30_000

                ev_queue    = _queue_mod.Queue()
                tc_results  = {}
                tc_args_map = {}
                tc_parse_errors = {}

                for tc in tc_list:
                    fn_name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        tc_parse_errors[tc["id"]] = None
                    except json.JSONDecodeError as e:
                        args = {}
                        tc_parse_errors[tc["id"]] = f"Error: could not parse tool arguments as JSON: {e}\nRaw arguments: {tc['function']['arguments']!r}"
                    tc_args_map[tc["id"]] = (fn_name, args)
                    yield f"data: {json.dumps({'type': 'tool_use', 'name': fn_name, 'args': args, 'tc_id': tc['id']})}\n\n"
                    if fn_name == "spawn_agent":
                        yield f"data: {json.dumps({'type': 'subagent_start', 'key': tc['id'], 'agent': args.get('agent_id','build'), 'task': args.get('task',''), 'context': args.get('context','')})}\n\n"

                def _run_tc(tc):
                    parse_err = tc_parse_errors.get(tc["id"])
                    if parse_err:
                        tc_results[tc["id"]] = parse_err
                        ev_queue.put({"_done": True, "tc_id": tc["id"]})
                        return
                    fn_name, args = tc_args_map[tc["id"]]
                    result = ""
                    try:
                        if fn_name == "spawn_agent":
                            with _subagent_semaphore:
                                def _on_event(evt):
                                    ev_queue.put({"_evt": True, "key": tc["id"], **evt})
                                result = run_subagent_streaming(
                                    agent_id     = args.get("agent_id", "build"),
                                    task         = args.get("task", ""),
                                    context      = args.get("context", ""),
                                    working_dirs = dirs,
                                    model        = model,
                                    depth        = 0,
                                    on_event     = _on_event,
                                )
                        else:
                            result = run_tool(fn_name, args)
                    except Exception as e:
                        result = f"Error: {e}"
                    finally:
                        raw = result if isinstance(result, str) else str(result)
                        if len(raw) > _TOOL_RESULT_MAX:
                            raw = raw[:_TOOL_RESULT_MAX] + f"\n\n[output truncated — {len(raw) - _TOOL_RESULT_MAX} chars omitted]"
                        tc_results[tc["id"]] = raw
                        ev_queue.put({"_done": True, "tc_id": tc["id"]})

                threads = []
                for tc in tc_list:
                    t = threading.Thread(target=_run_tc, args=(tc,), daemon=True)
                    t.start()
                    threads.append(t)

                done_count = 0
                while done_count < len(tc_list):
                    try:
                        evt = ev_queue.get(timeout=0.15)
                    except _queue_mod.Empty:
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                        continue
                    if evt.get("_done"):
                        done_count          += 1
                        tc_id               = evt["tc_id"]
                        fn_name, args       = tc_args_map[tc_id]
                        result              = tc_results.get(tc_id, "")
                        if fn_name == "spawn_agent":
                            yield f"data: {json.dumps({'type': 'subagent_done', 'key': tc_id, 'agent': args.get('agent_id','build'), 'result': result[:4000]})}\n\n"
                        yield f"data: {json.dumps({'type': 'tool_done', 'name': fn_name, 'tc_id': tc_id, 'result': result[:4000]})}\n\n"
                    elif evt.get("_evt"):
                        if evt.get("subtype") == "usage":
                            token_counts["input"]  += evt.get("input_tokens", 0)
                            token_counts["output"] += evt.get("output_tokens", 0)
                            _got_real_usage = True
                        yield f"data: {json.dumps({'type': 'subagent_stream', 'key': evt['key'], 'subtype': evt.get('subtype'), 'data': evt.get('data'), 'name': evt.get('name'), 'args': evt.get('args'), 'tc_id': evt.get('tc_id'), 'result': evt.get('result','')[:500] if evt.get('result') else None})}\n\n"

                for tc in tc_list:
                    fn_name, args = tc_args_map[tc["id"]]
                    result        = tc_results.get(tc["id"], "")
                    tr_turn       = {
                        "id":           _next_id(chat_id, "tr"),
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      result,
                    }
                    new_rich_turns.append(tr_turn)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

            if new_rich_turns:
                for t in new_rich_turns:
                    history.append(t)
                    state.chat_display_histories.setdefault(chat_id, []).append(t)
                state.chat_histories[chat_id] = history

            yield f"data: {json.dumps({'type': 'history_update', 'history': state.chat_display_histories.get(chat_id, history), 'api_history': state.chat_histories[chat_id]})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return Response(
            stream_with_context(generate()),
            content_type="text/event-stream",
            headers={
                "Cache-Control":     "no-cache",
                "X-Accel-Buffering": "no",
                "Connection":        "keep-alive",
            },
        )
