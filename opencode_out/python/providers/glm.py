"""
Free provider — GLM (Z-ai) via smanx proxy (z-ai/glm-5.1).
Endpoint: https://openai.good.hidns.vip/v1/chat/completions

Usage:
    from glm_provider import stream_chat, chat, MODELS, PROVIDER_NAME
"""

import json
import requests
from typing import Generator

PROVIDER_NAME = "glm"
MODELS = [
    {"id": "z-ai/glm-5.1", "label": "GLM 5.1", "ctx": 128000},
]

API_KEY = "https://github.com/smanx/free-api"
BASE_URL = "https://openai.good.hidns.vip"


def stream_chat(
    model_id: str,
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int | None = None,
) -> Generator[dict, None, None]:
    """Stream chat via GLM."""
    url = f"{BASE_URL}/v1/chat/completions"

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens or 4096,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, stream=True, timeout=120)
        response.raise_for_status()
    except Exception as e:
        yield {"type": "error", "text": str(e)}
        return

    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            yield {"type": "done"}
            return

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {})

        thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if thinking:
            yield {"type": "thinking", "text": thinking}

        content = delta.get("content") or ""
        if content:
            yield {"type": "text", "text": content}

        for tc_delta in delta.get("tool_calls", []):
            idx = tc_delta.get("index", 0)
            func = tc_delta.get("function", {})
            yield {
                "type": "tool_delta",
                "index": idx,
                "id": tc_delta.get("id", ""),
                "name": func.get("name", ""),
                "arguments": func.get("arguments", ""),
            }

    yield {"type": "done"}


def chat(
    model_id: str,
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Non-streaming chat via GLM."""
    url = f"{BASE_URL}/v1/chat/completions"

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens or 4096,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
    except Exception as e:
        return {"type": "error", "text": str(e)}

    try:
        data = response.json()
    except json.JSONDecodeError:
        return {"type": "error", "text": "Failed to parse response"}

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message", {})

    return {
        "type": "text",
        "text": message.get("content", ""),
        "thinking": message.get("reasoning_content", ""),
        "tool_calls": message.get("tool_calls", []),
    }
