"""
Free provider — opencode.ai (minimax-m2.5-free) via SSE.
"""
import json
import re
import requests
from typing import Generator

PROVIDER_NAME = "free"
MODELS = [
    {"id": "minimax-m2.5-free", "label": "MiniMax M2.5 Free", "ctx": 128000},
]

def stream_chat(model_id: str, messages: list[dict], tools: list | None = None,
                max_tokens: int | None = None) -> Generator[dict, None, None]:
    """Stream chat completions from opencode.ai SSE endpoint."""
    # model_id is ignored — we always use minimax-m2.5-free
    url = "https://opencode.ai/zen/v1/chat/completions"

    payload = {
        "model": "minimax-m2.5-free",
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens or 4096,
    }
    if tools:
        payload["tools"] = tools

    try:
        with requests.post(url, json=payload, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            buffer = ""
            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk
                # Process complete SSE lines (data: ...)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        yield {"type": "done"}
                        return
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # opencode SSE format
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # text
                    if "content" in delta:
                        yield {"type": "text", "text": delta["content"]}

                    # thinking (reasoning_thought in some formats)
                    thinking = delta.get("reasoning_thought") or delta.get("thinking")
                    if thinking:
                        yield {"type": "thinking", "text": thinking}

                    # tool calls
                    tool_calls = delta.get("tool_calls") or choice.get("tool_calls", [])
                    for tc in tool_calls:
                        idx = tc.get("index", 0)
                        func = tc.get("function", {})
                        yield {
                            "type": "tool_delta",
                            "index": idx,
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "arguments": func.get("arguments", ""),
                        }
    except Exception as e:
        yield {"type": "error", "text": str(e)}