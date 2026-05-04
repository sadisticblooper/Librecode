"""
Ollama provider for OpenCode.

API key provided via OLLAMA_API_KEY env var or config.
Uses api.ollama.com/api/chat for multi-turn support.
"""

import os
import json
import requests

API_URL = "https://api.ollama.com/api/chat"
PROVIDER_NAME = "Ollama"

API_KEY = os.environ.get("OLLAMA_API_KEY", "")

OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "126b2529063345a788165007a5b1e986.l-bDF-jxJTd6Z6cTP1s4C3JQ")

MODELS = [
    {"id": "gemma4:31b", "label": "gemma4:31b", "ctx": 32768},
    {"id": "ministral-3:14b", "label": "ministral-3:14b", "ctx": 32768},
    {"id": "devstral-2:123b", "label": "devstral-2:123b", "ctx": 65536},
    {"id": "gemma3:12b", "label": "gemma3:12b", "ctx": 32768},
    {"id": "ministral-3:8b", "label": "ministral-3:8b", "ctx": 32768},
    {"id": "rnj-1:8b", "label": "rnj-1:8b", "ctx": 32768},
    {"id": "qwen3-vl:235b", "label": "qwen3-vl:235b", "ctx": 65536},
    {"id": "ministral-3:3b", "label": "ministral-3:3b", "ctx": 32768},
    {"id": "gemma3:27b", "label": "gemma3:27b", "ctx": 65536},
    {"id": "nemotron-3-super", "label": "nemotron-3-super", "ctx": 128000},
    {"id": "glm-4.6", "label": "glm-4.6", "ctx": 200000},
    {"id": "gpt-oss:20b", "label": "gpt-oss:20b", "ctx": 32768},
    {"id": "devstral-small-2:24b", "label": "devstral-small-2:24b", "ctx": 32768},
    {"id": "qwen3-coder-next", "label": "qwen3-coder-next", "ctx": 32768},
    {"id": "gpt-oss:120b", "label": "gpt-oss:120b", "ctx": 65536},
    {"id": "cogito-2.1:671b", "label": "cogito-2.1:671b", "ctx": 200000},
    {"id": "qwen3-vl:235b-instruct", "label": "qwen3-vl:235b-instruct", "ctx": 65536},
    {"id": "minimax-m2", "label": "minimax-m2", "ctx": 200000},
    {"id": "minimax-m2.1", "label": "minimax-m2.1", "ctx": 200000},
    {"id": "gemma3:4b", "label": "gemma3:4b", "ctx": 32768},
    {"id": "qwen3-next:80b", "label": "qwen3-next:80b", "ctx": 131072},
    {"id": "nemotron-3-nano:30b", "label": "nemotron-3-nano:30b", "ctx": 32768},
    {"id": "mistral-large:675b", "label": "mistral-large:675b", "ctx": 200000},
    {"id": "minimax-m2.5", "label": "minimax-m2.5", "ctx": 200000, "slow": True},
    {"id": "qwen3-coder:480b", "label": "qwen3-coder:480b", "ctx": 200000, "slow": True},
    {"id": "glm-4.7", "label": "glm-4.7", "ctx": 200000, "slow": True},
]


def _convert_messages(messages: list) -> list:
    """Convert OpenAI-style messages to Ollama format."""
    out = []
    for m in messages:
        role = m.get("role", "user")
        if role == "assistant":
            role = "assistant"
        elif role == "system":
            role = "system"
        elif role == "tool":
            role = "tool"
        else:
            role = "user"

        msg = {"role": role, "content": m.get("content", "")}

        if m.get("tool_calls"):
            msg["tool_calls"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                }
                for tc in m["tool_calls"]
            ]

        out.append(msg)
    return out


def _convert_tools(tools: list) -> list | None:
    """Convert OpenAI tools to Ollama function declarations."""
    if not tools:
        return None
    declarations = []
    for tool in tools:
        fn = tool.get("function", {})
        declarations.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return [{"type": "function", "function": {"name": "tool", "parameters": declarations}}]


def stream_chat(model_id: str, messages: list, tools: list | None, max_tokens: int):
    """Generator yielding events for Ollama."""
    headers = {"Content-Type": "application/json"}
    if OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"

    payload = {
        "model": model_id,
        "messages": _convert_messages(messages),
        "stream": True,
        "options": {"num_predict": max_tokens},
    }

    tools_converted = _convert_tools(tools)
    if tools_converted:
        payload["tools"] = tools_converted

    try:
        resp = requests.post(API_URL, headers=headers, json=payload, stream=True, timeout=120)
        resp.raise_for_status()
    except Exception as e:
        yield {"type": "error", "text": str(e)}
        return

    tool_calls_acc = {}

    for raw in resp.iter_lines():
        if not raw:
            continue
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue

        msg = chunk.get("message", {})

        thinking_chunk = msg.get("thinking") or msg.get("reasoning_content") or ""
        if thinking_chunk:
            yield {"type": "thinking", "text": thinking_chunk}

        content_chunk = msg.get("content") or ""
        if content_chunk:
            yield {"type": "text", "text": content_chunk}

        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                idx = tc.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                if tc.get("id"):
                    tool_calls_acc[idx]["id"] = tc["id"]
                fn = tc.get("function", {})
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