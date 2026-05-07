"""
DeepAI — Meta family (llama-3.3-70b, llama-3.1-8b, llama-4-scout)
Tool calling via text-based scheme: model outputs <tool_calls>[...]</tool_calls>
"""

import json
import requests
from typing import Generator

PROVIDER_NAME = "deepai-meta"
MODELS = [
    {"id": "llama-3.3-70b-instruct", "label": "Llama 3.3 70B Instruct", "ctx": 128000},
    {"id": "llama-3.1-8b-instant",   "label": "Llama 3.1 8B Instant",  "ctx": 128000},
    {"id": "llama-4-scout",           "label": "Llama 4 Scout",         "ctx": 128000},
]

API_KEY = "tryit-73813154312-355d5cb436707b667d59c19a5fed7e20"
URL = "https://api.deepai.org/hacking_is_a_serious_crime"

_TOOL_CALL_OPEN = "<tool_calls>"
_TOOL_CALL_CLOSE = "</tool_calls>"


def _build_tool_system(tools: list) -> str:
    tool_specs = []
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "")
        desc = fn.get("description", "")
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])
        args_doc = []
        for k, v in props.items():
            req = " (required)" if k in required else ""
            typ = v.get("type", "any")
            pdesc = v.get("description", "")
            args_doc.append(f"  - {k} ({typ}){req}: {pdesc}")
        tool_specs.append(
            f"### {name}\n{desc}\nArguments:\n" + ("\n".join(args_doc) if args_doc else "  (none)")
        )
    return (
        "\n\n---\n"
        "## Tool Calling\n\n"
        "You have access to tools. If you need to call one or more tools, "
        "output your full response text first, then append a tool call block "
        "as the VERY LAST thing in your message.\n\n"
        "Format:\n"
        "<tool_calls>\n"
        '[{"name": "tool_name", "args": {"arg1": "val1"}}, ...]\n'
        "</tool_calls>\n\n"
        "Rules:\n"
        "- Only include the block if you are actually calling a tool.\n"
        "- All tool calls for a single turn go in one block as a JSON array.\n"
        "- After you receive tool results continue your response.\n\n"
        "Available tools:\n\n" + "\n\n".join(tool_specs)
    )


def _inject_tool_system(messages: list, tools: list) -> list:
    tool_instructions = _build_tool_system(tools)
    msgs = list(messages)
    if msgs and msgs[0].get("role") == "system":
        msgs[0] = dict(msgs[0])
        msgs[0]["content"] = msgs[0].get("content", "") + "\n" + tool_instructions
    else:
        msgs.insert(0, {"role": "system", "content": tool_instructions.strip()})
    return msgs


def _call_api(model_id: str, messages: list[dict]) -> tuple[int, str]:
    chat_history = json.dumps([{"role": m["role"], "content": m.get("content", "")} for m in messages])
    try:
        r = requests.post(URL, data={
            "chat_style": "chat",
            "chatHistory": chat_history,
            "model": model_id,
            "enabled_tools": "[]",
        }, headers={"api-key": API_KEY}, timeout=120)
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


def _parse_tool_calls(text: str) -> tuple[str, list]:
    if _TOOL_CALL_OPEN not in text:
        return text, []
    before, _, after = text.partition(_TOOL_CALL_OPEN)
    raw_block = after.split(_TOOL_CALL_CLOSE)[0].strip()
    try:
        tool_calls = json.loads(raw_block)
        if not isinstance(tool_calls, list):
            tool_calls = []
    except json.JSONDecodeError:
        tool_calls = []
    return before.strip(), tool_calls


def stream_chat(model_id, messages, tools=None, max_tokens=None):
    if tools:
        messages = _inject_tool_system(messages, tools)
    status, text = _call_api(model_id, messages)
    if status == 401:
        yield {"type": "error", "text": "Pro model, not available anonymously"}
        return
    if status != 200:
        yield {"type": "error", "text": f"HTTP {status}: {text[:200]}"}
        return
    text, tool_calls = _parse_tool_calls(text)
    for i in range(0, len(text), 20):
        yield {"type": "text", "text": text[i:i + 20]}
    for i, tc in enumerate(tool_calls):
        yield {"type": "tool_delta", "index": i, "id": "", "name": tc.get("name", ""), "arguments": json.dumps(tc.get("args", tc.get("arguments", {})))}
    yield {"type": "done"}


def chat(model_id, messages, tools=None, max_tokens=None):
    if tools:
        messages = _inject_tool_system(messages, tools)
    status, text = _call_api(model_id, messages)
    if status == 401:
        return {"type": "error", "text": "Pro model, not available anonymously"}
    if status != 200:
        return {"type": "error", "text": f"HTTP {status}: {text[:200]}"}
    text, tool_calls = _parse_tool_calls(text)
    return {"type": "text", "text": text, "tool_calls": tool_calls}
