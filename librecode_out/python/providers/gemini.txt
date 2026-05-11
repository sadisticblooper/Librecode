"""
Gemini provider — converts OpenAI messages to Gemini format,
calls streamGenerateContent, yields normalized events.
"""
import json
import requests
from typing import Generator

PROVIDER_NAME = "gemini"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
API_KEY = ""  # Set your Gemini API key here

# Gemini models available via this provider
MODELS = [
    {"id": "gemini-2.0-flash",        "label": "Gemini 2.0 Flash",        "ctx": 128000},
    {"id": "gemini-2.5-pro",          "label": "Gemini 2.5 Pro",          "ctx": 128000},
    {"id": "gemini-2.5-flash",        "label": "Gemini 2.5 Flash",        "ctx": 128000},
    {"id": "gemini-3.0-flash",        "label": "Gemini 3.0 Flash",        "ctx": 128000},
    {"id": "gemini-3.0-flash-preview", "label": "Gemini 3.0 Flash Prev",  "ctx": 128000},
]


def _convert_to_gemini_format(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-format messages to Gemini Content list."""
    contents = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else ("user" if role == "user" else "user")
        parts = []
        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    parts.append({"text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    img_url = part.get("image_url", {}).get("url", "")
                    if img_url.startswith("data:"):
                        mime, b64 = img_url.split(";", 1)
                        mime = mime.replace("data:", "")
                        b64 = b64.replace("base64,", "")
                        parts.append({
                            "inlineData": {"mimeType": mime, "data": b64}
                        })
        else:
            parts.append({"text": str(content)})
        contents.append({"role": gemini_role, "parts": parts})
    return contents


def stream_chat(model_id: str, messages: list[dict], tools: list | None = None,
                max_tokens: int | None = None) -> Generator[dict, None, None]:
    """Stream from Gemini streamGenerateContent endpoint."""
    if not API_KEY:
        yield {"type": "error", "text": "GEMINI_API_KEY not set in providers/gemini.py"}
        return

    contents = _convert_to_gemini_format(messages)
    url = f"{API_BASE}/{model_id}:streamGenerateContent?key={API_KEY}&alt=sse"

    payload = {"contents": contents, "generationConfig": {}}
    if max_tokens:
        payload["generationConfig"]["maxOutputTokens"] = max_tokens

    if tools:
        funcs = []
        for tool in tools:
            for func in tool.get("function", {}).get("declarations", []):
                funcs.append(func)
        if funcs:
            payload["tools"] = [{"function_declarations": funcs}]

    try:
        with requests.post(url, json=payload, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            buffer = ""
            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk
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
                    candidates = data.get("candidates", [])
                    for cand in candidates:
                        content = cand.get("content", {})
                        for part in content.get("parts", []):
                            if "text" in part:
                                yield {"type": "text", "text": part["text"]}
                            fn = part.get("functionCall", {})
                            if fn:
                                yield {
                                    "type": "tool_delta",
                                    "index": 0,
                                    "id": fn.get("id", ""),
                                    "name": fn.get("name", ""),
                                    "arguments": json.dumps(fn.get("args", {})),
                                }
                        if cand.get("finishReason", "") in ("STOP", "MAX_TOKENS", "OTHER"):
                            yield {"type": "done"}
                            return
    except Exception as e:
        yield {"type": "error", "text": str(e)}