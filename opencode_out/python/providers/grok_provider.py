"""
Free provider — Grok via smanx proxy (grok-4.20-fast).
Endpoint: https://openai.good.hidns.vip/v1/chat/completions

Note: Grok via this proxy doesn't support tool calling.

Usage:
    from grok_provider import stream_chat, chat, MODELS, PROVIDER_NAME
"""

import json
import requests
from typing import Generator

PROVIDER_NAME = "grok"
MODELS = [
    {"id": "grok-4.20-fast", "label": "Grok 4.20 Fast", "ctx": 128000},
]

API_KEY = "https://github.com/smanx/free-api"
BASE_URL = "https://openai.good.hidns.vip"


def stream_chat(
    model_id: str,
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int | None = None,
) -> Generator[dict, None, None]:
    """Stream chat via Grok."""
    url = f"{BASE_URL}/v1/chat/completions"

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens or 4096,
    }

    # Grok doesn't support tools via this proxy
    if tools:
        yield {"type": "error", "text": "Grok via smanx proxy does not support tool calling."}

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

    yield {"type": "done"}


def chat(
    model_id: str,
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Non-streaming chat via Grok."""
    url = f"{BASE_URL}/v1/chat/completions"

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens or 4096,
    }

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
    }