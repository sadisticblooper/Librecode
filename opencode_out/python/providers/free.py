import os
import json
import requests

API_URL = "https://opencode.ai/zen/v1/chat/completions"
PROVIDER_NAME = "Free"

MODELS = [
    {"id": "minimax-m2.5-free", "label": "minimax-m2.5", "ctx": 1000000},
    {"id": "big-pickle-free", "label": "big-pickle", "ctx": 128000},
    {"id": "gpt-5-nano-free", "label": "gpt-5-nano", "ctx": 128000},
    {"id": "nemotron-3-super-free", "label": "nemotron-3-super", "ctx": 128000},
]


def stream_chat(model_id: str, messages: list, tools: list | None, max_tokens: int):
    """Generator yielding events for the Free (opencode.ai zen) endpoint."""
    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        resp = requests.post(API_URL, json=payload, stream=True, timeout=600)
        resp.raise_for_status()
    except Exception as e:
        yield {"type": "error", "text": str(e)}
        return

    tool_calls_acc = {}

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
        delta = choice.get("delta", {})

        thinking_chunk = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking") or ""
        if thinking_chunk:
            yield {"type": "thinking", "text": thinking_chunk}

        content_chunk = delta.get("content") or ""
        if content_chunk:
            yield {"type": "text", "text": content_chunk}

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

    if tool_calls_acc:
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            yield {
                "type": "tool_delta",
                "index": idx,
                "id": tc["id"],
                "name": tc["name"],
                "arguments": tc["arguments"],
            }

    yield {"type": "done"}