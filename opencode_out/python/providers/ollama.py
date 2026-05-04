"""
Ollama provider — streams from https://api.ollama.com/api/chat (NDJSON).
Bearer token auth. Models are listed explicitly below.
"""
import json
import requests
from typing import Generator

PROVIDER_NAME = "ollama"
API_KEY = "126b2529063345a788165007a5b1e986.l-bDF-jxJTd6Z6cTP1s4C3JQ"
API_BASE = "https://api.ollama.com/api/chat"

MODELS = [
    {"id": "gemma4:31b",           "label": "Gemma 4 31B",              "ctx": 128000},
    {"id": "ministral-3:14b",      "label": "Ministral 3 14B",           "ctx": 128000},
    {"id": "devstral-2:123b",      "label": "Devstral 2 123B",           "ctx": 128000},
    {"id": "gemma3:12b",           "label": "Gemma 3 12B",               "ctx": 128000},
    {"id": "ministral-3:8b",       "label": "Ministral 3 8B",            "ctx": 128000},
    {"id": "rnj-1:8b",             "label": "RNJ-1 8B",                  "ctx": 128000},
    {"id": "qwen3-vl:235b",        "label": "Qwen3 VL 235B",             "ctx": 128000},
    {"id": "ministral-3:3b",       "label": "Ministral 3 3B",            "ctx": 128000},
    {"id": "gemma3:27b",           "label": "Gemma 3 27B",               "ctx": 128000},
    {"id": "nemotron-3-super",     "label": "Nemotron-3 Super",          "ctx": 128000},
    {"id": "glm-4.6",              "label": "GLM-4.6",                   "ctx": 128000},
    {"id": "gpt-oss:20b",          "label": "GPT-OSS 20B",               "ctx": 128000},
    {"id": "devstral-small-2:24b", "label": "Devstral Small 2 24B",     "ctx": 128000},
    {"id": "qwen3-coder-next",     "label": "Qwen3 Coder Next",          "ctx": 128000},
    {"id": "gpt-oss:120b",         "label": "GPT-OSS 120B",              "ctx": 128000},
    {"id": "cogito-2.1:671b",      "label": "Cogito 2.1 671B",           "ctx": 128000},
    {"id": "minimax-m2",           "label": "MiniMax M2",                 "ctx": 128000},
    {"id": "minimax-m2.1",         "label": "MiniMax M2.1",               "ctx": 128000},
    {"id": "gemma3:4b",            "label": "Gemma 3 4B",                 "ctx": 128000},
    {"id": "qwen3-next:80b",       "label": "Qwen3 Next 80B",            "ctx": 128000},
    {"id": "nemotron-3-nano:30b",  "label": "Nemotron-3 Nano 30B",       "ctx": 128000},
    {"id": "mistral-large-3:675b", "label": "Mistral Large 3 675B",      "ctx": 128000},
]

def stream_chat(model_id: str, messages: list[dict], tools: list | None = None,
                max_tokens: int | None = None) -> Generator[dict, None, None]:
    """Stream from Ollama /api/chat endpoint (NDJSON lines)."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
    }
    if max_tokens:
        payload["options"] = {"num_predict": max_tokens}
    if tools:
        payload["tools"] = tools

    try:
        with requests.post(API_BASE, json=payload, headers=headers, stream=True,
                           timeout=300) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("message", {}).get("role")
                content = data.get("message", {}).get("content", "")

                # text content
                if content:
                    yield {"type": "text", "text": content}

                # thinking / reason_content
                thinking = data.get("message", {}).get("reasoning_content") or \
                           data.get("reasoning_content") or \
                           data.get("thinking")
                if thinking:
                    yield {"type": "thinking", "text": thinking}

                # tool calls
                tool_calls = data.get("tool_calls", [])
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

                # done signal
                if data.get("done"):
                    yield {"type": "done"}
                    return
    except Exception as e:
        yield {"type": "error", "text": str(e)}