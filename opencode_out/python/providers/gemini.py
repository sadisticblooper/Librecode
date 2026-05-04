"""
Gemini provider for OpenCode.

API key provided via GEMINI_API_KEY env var or config.
Uses google.genai SDK format.
"""

import os
import json
import requests

API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
PROVIDER_NAME = "Gemini"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


MODELS = [
    {"id": "gemini-2.0-flash", "label": "gemini-2.0-flash", "ctx": 1000000},
    {"id": "gemini-2.0-flash-lite", "label": "gemini-2.0-flash-lite", "ctx": 1000000},
    {"id": "gemini-1.5-pro", "label": "gemini-1.5-pro", "ctx": 2000000},
    {"id": "gemini-1.5-flash", "label": "gemini-1.5-flash", "ctx": 1000000},
    {"id": "gemini-1.5-flash-8b", "label": "gemini-1.5-flash-8b", "ctx": 1000000},
]


def _convert_messages(messages: list) -> list:
    """Convert OpenAI-style messages to Gemini format."""
    out = []
    for m in messages:
        role = m.get("role", "user")
        if role == "assistant":
            role = "model"
        elif role == "system":
            role = "user"
        else:
            role = "user"

        content = m.get("content", "")
        if isinstance(content, str):
            parts = [{"text": content}]
        elif isinstance(content, list):
            parts = [{"text": p.get("text", "")} for p in content if p.get("type") == "text"]
        else:
            parts = [{"text": str(content)}]

        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                parts.append(
                    {
                        "functionCall": {
                            "name": tc["function"]["name"],
                            "args": json.loads(tc["function"]["arguments"]),
                        }
                    }
                )

        out.append({"role": role, "parts": parts})

        if role == "tool":
            out[-1]["parts"] = [{"text": m.get("content", "")}]

    return out


def _convert_tools(tools: list) -> list | None:
    """Convert OpenAI tools to Gemini function declarations."""
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
    return declarations


def stream_chat(model_id: str, messages: list, tools: list | None, max_tokens: int):
    """Generator yielding events for Gemini."""
    if not GEMINI_API_KEY:
        yield {"type": "error", "text": "GEMINI_API_KEY not set"}
        return

    model_name = model_id
    if not model_name.startswith("models/"):
        model_name = f"models/{model_id}"

    url = f"{API_URL}/{model_name}:streamGenerateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": _convert_messages(messages),
        "generationConfig": {
            "temperature": 1.0,
            "maxOutputTokens": max_tokens,
            "stream": True,
        },
    }

    tools_converted = _convert_tools(tools)
    if tools_converted:
        payload["tools"] = [{"functionDeclarations": tools_converted}]

    try:
        resp = requests.post(url, json=payload, stream=True, timeout=120)
        resp.raise_for_status()
    except Exception as e:
        yield {"type": "error", "text": str(e)}
        return

    tool_calls_acc = {}
    accumulated_text = ""

    for raw in resp.iter_lines():
        if not raw:
            continue
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue

        candidate = chunk.get("candidates", [{}])[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        for part in parts:
            thinking = part.get("thought")
            if thinking:
                yield {"type": "thinking", "text": thinking}

            text = part.get("text", "")
            if text:
                accumulated_text += text
                yield {"type": "text", "text": text}

            func_call = part.get("functionCall")
            if func_call:
                idx = 0
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                tool_calls_acc[idx]["name"] += func_call.get("name", "")
                args = func_call.get("args", {})
                if isinstance(args, str):
                    tool_calls_acc[idx]["arguments"] += args
                else:
                    tool_calls_acc[idx]["arguments"] += json.dumps(args)

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