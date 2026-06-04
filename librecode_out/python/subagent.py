"""
Subagent execution: run_subagent (blocking) and run_subagent_streaming
(streaming with live event callbacks).
"""

import os
import json
import threading

import python.agents as agents_mod
import python.state as state
from python.config import COMPACTION_MODEL, MAX_TOKENS
from python.providers import get_provider
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

    # Use provider for API call (handles auth/models correctly)
    provider = get_provider(model or COMPACTION_MODEL)
    effective_model = model or COMPACTION_MODEL

    for _round in range(20):
        tool_calls_acc  = {}
        round_content   = ""

        try:
            events = provider.stream_chat(effective_model, messages, active_tools, MAX_TOKENS)
        except Exception as e:
            return f"Error: subagent API call failed: {e}"

        for event in events:
            evtype = event.get("type")
            if evtype == "text":
                round_content += event["text"]
            elif evtype == "thinking":
                pass  # accumulate if needed
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
            elif evtype == "error":
                return f"Error: subagent API call failed: {event['text']}"
            elif evtype == "done":
                pass

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

        content = round_content
        messages.append({"role": "assistant", "content": content, "tool_calls": tc_list})

        for tc in tc_list:
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

    return "Error: subagent hit the round limit without a final answer. Task may be too large or agent is stuck in a loop."


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

    # Use provider for API call (handles auth/models correctly)
    provider = get_provider(model or COMPACTION_MODEL)
    effective_model = model or COMPACTION_MODEL

    for _round in range(20):
        tool_calls_acc  = {}
        round_content   = ""

        try:
            events = provider.stream_chat(effective_model, messages, active_tools, MAX_TOKENS)
        except Exception as e:
            return f"Error: subagent API call failed: {e}"

        for event in events:
            evtype = event.get("type")
            if evtype == "text":
                round_content += event["text"]
                _emit({"subtype": "text", "data": event["text"]})
            elif evtype == "thinking":
                _emit({"subtype": "thinking", "data": event["text"]})
            elif evtype == "usage":
                _emit({"subtype": "usage", "input_tokens": event.get("input_tokens", 0), "output_tokens": event.get("output_tokens", 0)})
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
            elif evtype == "error":
                return f"Error: subagent API call failed: {event['text']}"
            elif evtype == "done":
                pass

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

    return "Error: subagent hit the round limit without a final answer. Task may be too large or agent is stuck in a loop."