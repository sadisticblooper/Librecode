"""
Subagent execution: run_subagent (blocking) and run_subagent_streaming
(streaming with live event callbacks).
"""

import os
import json
import threading
import requests

import python.agents as agents_mod
import python.state as state
from python.config import API_URL, MODEL, MAX_TOKENS
from python.tools import run_tool, get_tools_for_agent


_subagent_semaphore = threading.Semaphore(5)

_MAX_DEPTH = 2   # main(0) → sub(1) → sub-sub(2) → no more spawning


def _build_subagent_system(agent_id: str, working_dirs: list) -> str:
    """Build the system message content for a subagent."""
    profile      = agents_mod.AGENT_PROFILES.get(agent_id, {})
    agent_suffix = profile.get("system_suffix", "")
    base_prompt  = (agents_mod.SYSTEM_PROMPT_BASE + "\n\n") if agents_mod.SYSTEM_PROMPT_BASE else ""

    if working_dirs:
        hints = []
        for d in working_dirs:
            try:
                files = sorted(os.listdir(d))[:30]
                hints.append(f"Folder: {d}\n" + "\n".join(files))
            except Exception:
                hints.append(f"Folder: {d} (unreadable)")
        dir_info = "\n\n".join(hints)
        return f"{base_prompt}You are a subagent. {dir_info}\n\n---\n{agent_suffix}"
    return f"{base_prompt}You are a subagent.\n\n---\n{agent_suffix}"


def run_subagent(
    agent_id: str,
    task: str,
    context: str = "",
    working_dirs: list = None,
    model: str = None,
    depth: int = 0,
) -> str:
    """
    Run an agent profile as a subagent (blocking, non-streaming).
    Returns the final text output.
    depth prevents infinite recursion — subagents at depth >= _MAX_DEPTH
    do not receive the spawn_agent tool.
    """
    profile = agents_mod.AGENT_PROFILES.get(agent_id)
    if not profile:
        return f"Error: unknown agent '{agent_id}'"

    dirs         = working_dirs or ([state.working_dir] if state.working_dir else [])
    active_tools = get_tools_for_agent(agent_id)
    if depth >= _MAX_DEPTH:
        active_tools = [t for t in active_tools if t["function"]["name"] != "spawn_agent"]

    system_content = _build_subagent_system(agent_id, dirs)
    user_content   = task if not context else f"Context:\n{context}\n\n---\n\nTask:\n{task}"

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]

    for _round in range(20):
        payload = {
            "model":      model or MODEL,
            "messages":   messages,
            "max_tokens": MAX_TOKENS,
        }
        if active_tools:
            payload["tools"]       = active_tools
            payload["tool_choice"] = "auto"

        try:
            resp = requests.post(API_URL, json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return f"Error: subagent API call failed: {e}"

        choice     = (data.get("choices") or [{}])[0]
        message    = choice.get("message", {})
        content    = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            return content.strip() or "(subagent returned no output)"

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            if fn_name == "spawn_agent":
                result = run_subagent(
                    agent_id     = args.get("agent_id", "build"),
                    task         = args.get("task", ""),
                    context      = args.get("context", ""),
                    working_dirs = working_dirs,
                    model        = model,
                    depth        = depth + 1,
                )
            else:
                result = run_tool(fn_name, args)

            messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })

    return "(subagent hit round limit without producing a final answer)"


def run_subagent_streaming(
    agent_id: str,
    task: str,
    context: str = "",
    working_dirs: list = None,
    model: str = None,
    depth: int = 0,
    on_event=None,
) -> str:
    """
    Streaming version of run_subagent.
    Calls on_event(dict) for each meaningful event so the UI can render
    the subagent's activity live. Returns final text result.
    """
    profile = agents_mod.AGENT_PROFILES.get(agent_id)
    if not profile:
        return f"Error: unknown agent '{agent_id}'"

    dirs         = working_dirs or ([state.working_dir] if state.working_dir else [])
    active_tools = get_tools_for_agent(agent_id)
    if depth >= _MAX_DEPTH:
        active_tools = [t for t in active_tools if t["function"]["name"] != "spawn_agent"]

    system_content = _build_subagent_system(agent_id, dirs)
    user_content   = task if not context else f"Context:\n{context}\n\n---\n\nTask:\n{task}"

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]

    def _emit(evt: dict) -> None:
        if on_event:
            try:
                on_event(evt)
            except Exception:
                pass

    for _round in range(20):
        payload = {
            "model":      model or MODEL,
            "messages":   messages,
            "max_tokens": MAX_TOKENS,
            "stream":     True,
        }
        if active_tools:
            payload["tools"]       = active_tools
            payload["tool_choice"] = "auto"

        try:
            resp = requests.post(API_URL, json=payload, stream=True, timeout=300)
            resp.raise_for_status()
        except Exception as e:
            return f"Error: subagent API call failed: {e}"

        tool_calls_acc: dict = {}
        round_content        = ""

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choice = (chunk.get("choices") or [{}])[0]
            delta  = choice.get("delta", {})

            thinking_chunk = (
                delta.get("reasoning_content")
                or delta.get("reasoning")
                or delta.get("thinking")
                or ""
            )
            if thinking_chunk:
                _emit({"subtype": "thinking", "data": thinking_chunk})

            content_chunk = delta.get("content") or ""
            if content_chunk:
                round_content += content_chunk
                _emit({"subtype": "text", "data": content_chunk})

            for tc_delta in delta.get("tool_calls", []):
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                if tc_delta.get("id"):
                    tool_calls_acc[idx]["id"] = tc_delta["id"]
                fn = tc_delta.get("function", {})
                if fn.get("name"):
                    tool_calls_acc[idx]["name"] += fn["name"]
                if fn.get("arguments"):
                    tool_calls_acc[idx]["arguments"] += fn["arguments"]

        if not tool_calls_acc:
            return round_content.strip() or "(subagent returned no output)"

        tc_list = [
            {
                "id":   tool_calls_acc[idx]["id"] or f"sub-tc-{_round}-{idx}",
                "type": "function",
                "function": {
                    "name":      tool_calls_acc[idx]["name"],
                    "arguments": tool_calls_acc[idx]["arguments"],
                },
            }
            for idx in sorted(tool_calls_acc.keys())
        ]

        messages.append({
            "role":       "assistant",
            "content":    round_content,
            "tool_calls": tc_list,
        })

        for tc in tc_list:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            _emit({"subtype": "tool_use", "name": fn_name, "args": args, "tc_id": tc["id"]})

            if fn_name == "spawn_agent":
                result = run_subagent_streaming(
                    agent_id     = args.get("agent_id", "build"),
                    task         = args.get("task", ""),
                    context      = args.get("context", ""),
                    working_dirs = working_dirs,
                    model        = model,
                    depth        = depth + 1,
                    on_event     = on_event,
                )
            else:
                result = run_tool(fn_name, args)

            _emit({"subtype": "tool_done", "name": fn_name, "tc_id": tc["id"], "result": result})

            messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })

    return "(subagent hit round limit without producing a final answer)"
