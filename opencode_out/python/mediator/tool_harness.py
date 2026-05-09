"""
tool_harness.py — Fake tool-call support for models that don't natively support it.

When a provider manifest has "supports_tools": false but the request includes
tools, this harness:
  1. Prepends a system prompt instructing the model to use <tool_call> XML.
  2. Parses <tool_call> blocks from the model's text response.
  3. Returns OpenAI-format tool_calls.
"""

import json
import re
from typing import Optional


SYSTEM_PREFIX_TEMPLATE = """\
You have access to tools. To call a tool, respond ONLY with this exact format:

<tool_call>
{{"name": "tool_name", "arguments": {{"key": "value"}}}}
</tool_call>

If you do not need a tool, respond normally.
Available tools:
{tools_json}
---
"""


def wrap(message: str, tools: list[dict]) -> str:
    """Prepend tool instructions to the outgoing message."""
    tools_json = json.dumps(tools, indent=2)
    prefix = SYSTEM_PREFIX_TEMPLATE.format(tools_json=tools_json)
    return prefix + message


def parse(text: str) -> tuple[Optional[str], list[dict]]:
    """
    Extract <tool_call> blocks from model response.

    Returns
    -------
    (clean_text, tool_calls)
      clean_text  : text with <tool_call> blocks removed
      tool_calls  : list of OpenAI-format tool_call dicts (may be empty)
    """
    pattern = re.compile(r'<tool_call>(.*?)</tool_call>', re.DOTALL)
    tool_calls = []
    call_id_counter = 0

    def _handle(m):
        nonlocal call_id_counter
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            call_id_counter += 1
            tool_calls.append({
                "id":   f"harness_{call_id_counter}",
                "type": "function",
                "function": {
                    "name":      obj.get("name", "unknown"),
                    "arguments": json.dumps(obj.get("arguments", {})),
                },
            })
        except json.JSONDecodeError:
            pass
        return ""

    clean_text = pattern.sub(_handle, text).strip()
    return clean_text or None, tool_calls
