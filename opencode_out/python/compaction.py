"""
Context compaction for OpenCode Android.

How it works (mirrors opencode desktop logic):
  1. Token usage is estimated after each assistant reply.
  2. When total tokens exceed COMPACTION_THRESHOLD, compaction triggers.
  3. The conversation history is split into a HEAD (older turns, to be summarised)
     and a TAIL (the most recent turns, kept verbatim).
  4. A summary is generated for HEAD using the compaction_summary prompt.
  5. Future requests are sent as:
       [system]  <normal system prompt>
       [user]    <compaction marker with summary>
       ...       <TAIL messages verbatim>
       [user]    <current user message>

Constants mirror opencode desktop values (scaled for mobile budgets):
  PRUNE_MINIMUM          – min tokens freed before tool-output pruning fires
  PRUNE_PROTECT          – tokens of tool output that are always kept
  TOOL_OUTPUT_MAX_CHARS  – hard cap on any single tool output sent in a compact request
  TAIL_TURNS             – number of recent turns always kept verbatim
  MIN/MAX_PRESERVE_TOKENS– bounds for the tail token budget
"""

import os
import json
import requests

# ── tunables ──────────────────────────────────────────────────────────────────
PRUNE_MINIMUM           = 20_000   # min tokens freed before pruning activates
PRUNE_PROTECT           = 40_000   # tokens of tool output never pruned
TOOL_OUTPUT_MAX_CHARS   = 2_000    # max chars of any tool output in compact req
TAIL_TURNS              = 2        # recent turns kept verbatim
MIN_PRESERVE_TOKENS     = 2_000
MAX_PRESERVE_TOKENS     = 8_000
COMPACTION_BUFFER       = 20_000   # reserved for model output

# ── prompt loading ─────────────────────────────────────────────────────────────
_PROMPTS_DIR = os.path.dirname(__file__)

def _load_prompt(name: str) -> str:
    """Load a prompt file from the prompts/ directory."""
    path = os.path.join(_PROMPTS_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _summary_template() -> str:
    return _load_prompt("compaction_summary.txt")

def _new_prompt() -> str:
    return _load_prompt("compaction_new.txt")

def _update_prompt(previous_summary: str) -> str:
    template = _load_prompt("compaction_update.txt")
    return template.replace("{previous_summary}", previous_summary)

def _continue_prompt() -> str:
    return _load_prompt("compaction_continue.txt")

# ── token estimation ───────────────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """Fast approximation: ~4 chars per token."""
    return max(1, len(text) // 4)

def estimate_messages_tokens(messages: list) -> int:
    return sum(estimate_tokens(json.dumps(m)) for m in messages)

# ── usable context budget ──────────────────────────────────────────────────────
def usable_tokens(context_limit: int, max_output_tokens: int) -> int:
    """
    How many input tokens are actually usable before we must compact.
    We reserve space for the model's output + compaction buffer.
    """
    reserved = min(COMPACTION_BUFFER, max_output_tokens)
    return max(0, context_limit - max_output_tokens - reserved)

def is_overflow(
    total_tokens: int,
    context_limit: int,
    max_output_tokens: int,
) -> bool:
    """Return True when we should trigger compaction."""
    limit = usable_tokens(context_limit, max_output_tokens)
    return limit > 0 and total_tokens >= limit

# ── tail / head splitting ──────────────────────────────────────────────────────
def _tail_budget(context_limit: int, max_output_tokens: int) -> int:
    """Token budget for the verbatim tail."""
    usable = usable_tokens(context_limit, max_output_tokens)
    return min(MAX_PRESERVE_TOKENS, max(MIN_PRESERVE_TOKENS, usable // 4))

def _user_turn_indices(messages: list) -> list:
    """
    Return a list of (start_index, end_index) for each user-initiated turn.
    A turn spans from the user message up to (but not including) the next
    user message.
    """
    starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    turns = []
    for j, s in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(messages)
        turns.append((s, end))
    return turns

def split_head_tail(
    messages: list,
    context_limit: int,
    max_output_tokens: int,
) -> tuple:
    """
    Split messages into (head, tail).

    tail  – the most recent TAIL_TURNS turns that fit within the tail budget
    head  – everything before the tail (will be summarised)

    Returns (head_messages, tail_messages).
    If there is nothing to split (too few turns), returns (messages, []).
    """
    turns = _user_turn_indices(messages)
    if len(turns) <= 1:
        return messages, []

    budget = _tail_budget(context_limit, max_output_tokens)
    recent = turns[-TAIL_TURNS:]

    total = 0
    keep_from = None
    for start, end in reversed(recent):
        segment = messages[start:end]
        size = estimate_messages_tokens(segment)
        if total + size <= budget:
            total += size
            keep_from = start
        else:
            # Try to keep at least the last part of this turn
            for split in range(start + 1, end):
                seg = messages[split:end]
                if estimate_messages_tokens(seg) <= budget - total:
                    keep_from = split
                    break
            break

    if keep_from is None or keep_from == 0:
        return messages, []

    return messages[:keep_from], messages[keep_from:]

# ── tool-output pruning ────────────────────────────────────────────────────────
def prune_tool_outputs(messages: list) -> list:
    """
    Truncate old tool outputs to save context space.

    Logic (mirrors opencode desktop):
      – Walk backwards through messages summing tool-output sizes.
      – Once we have seen PRUNE_PROTECT tokens worth of tool output, truncate
        anything older to TOOL_OUTPUT_MAX_CHARS.
      – Only fire if doing so would free at least PRUNE_MINIMUM tokens.
    """
    msgs = [m.copy() for m in messages]  # shallow copy
    total = 0
    prunable = []

    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.get("role") != "tool":
            continue
        content = m.get("content", "")
        size = estimate_tokens(content if isinstance(content, str) else json.dumps(content))
        total += size
        if total > PRUNE_PROTECT:
            prunable.append(i)

    freed = sum(
        estimate_tokens(
            msgs[i].get("content", "")
            if isinstance(msgs[i].get("content", ""), str)
            else json.dumps(msgs[i].get("content", ""))
        )
        for i in prunable
    )

    if freed < PRUNE_MINIMUM:
        return msgs  # not worth it

    for i in prunable:
        content = msgs[i].get("content", "")
        if isinstance(content, str) and len(content) > TOOL_OUTPUT_MAX_CHARS:
            msgs[i]["content"] = content[:TOOL_OUTPUT_MAX_CHARS] + "\n[output truncated]"

    return msgs

# ── summary generation ─────────────────────────────────────────────────────────
def build_summary_prompt(head_messages: list, previous_summary: str | None) -> str:
    """
    Build the user-turn content that asks the model to produce a summary.
    """
    anchor = _update_prompt(previous_summary) if previous_summary else _new_prompt()
    template = _summary_template()

    # Render a readable transcript of the head
    lines = []
    for m in head_messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if not content:
            # assistant with tool_calls only – describe briefly
            tcs = m.get("tool_calls", [])
            if tcs:
                names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tcs)
                content = f"[called tools: {names}]"
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") if p.get("type") == "text" else f"[{p.get('type', '?')}]"
                for p in content
            )
        # Truncate very long messages (tool outputs etc.)
        if len(content) > TOOL_OUTPUT_MAX_CHARS:
            content = content[:TOOL_OUTPUT_MAX_CHARS] + " [truncated]"
        lines.append(f"{role.upper()}: {content}")

    transcript = "\n\n".join(lines)
    return "\n\n".join([transcript, anchor, template])

def generate_summary(
    api_url: str,
    model: str,
    head_messages: list,
    previous_summary: str | None,
    extra_headers: dict | None = None,
) -> str | None:
    """
    Call the LLM to produce a compaction summary for head_messages.
    Returns the summary string, or None on failure.
    """
    prompt = build_summary_prompt(head_messages, previous_summary)
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 2000,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        # Handle both OpenAI-style and Anthropic-style responses
        choices = data.get("choices")
        if choices:
            text = choices[0].get("message", {}).get("content", "")
        else:
            content_blocks = data.get("content", [])
            text = " ".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        return text.strip() or None
    except Exception:
        return None

# ── main compact helper ────────────────────────────────────────────────────────
def compact_messages(
    messages: list,
    system_messages: list,
    api_url: str,
    model: str,
    previous_summary: str | None,
    context_limit: int,
    max_output_tokens: int,
    extra_headers: dict | None = None,
) -> tuple:
    """
    High-level entry point called from the Flask route.

    Returns:
        (compacted_messages, new_summary, did_compact)

    compacted_messages  – the message list to actually send to the API
    new_summary         – updated summary string (store this for the next request)
    did_compact         – True if compaction actually ran
    """
    all_messages = system_messages + messages
    total = estimate_messages_tokens(all_messages)

    if not is_overflow(total, context_limit, max_output_tokens):
        return messages, previous_summary, False

    # Step 1 – optionally prune tool outputs first (cheap, no API call)
    pruned = prune_tool_outputs(messages)

    # Step 2 – split into head (summarise) and tail (keep verbatim)
    head, tail = split_head_tail(pruned, context_limit, max_output_tokens)
    if not head:
        # Nothing to compact – return as-is
        return pruned, previous_summary, False

    # Step 3 – generate summary for the head
    summary = generate_summary(api_url, model, head, previous_summary, extra_headers)
    if not summary:
        # Summary failed – fall back to just the tail to avoid hard crash
        return tail if tail else pruned, previous_summary, False

    # Step 4 – build compacted history:
    #   [compaction marker message] + tail
    compaction_marker = {
        "role": "user",
        "content": f"[Context compacted]\n\n{summary}",
        "_compaction": True,   # internal flag, stripped before sending
    }

    compacted = [compaction_marker] + tail
    return compacted, summary, True

def build_compacted_messages_for_api(compacted: list) -> list:
    """Strip internal markers before sending to the API."""
    out = []
    for m in compacted:
        clean = {k: v for k, v in m.items() if not k.startswith("_")}
        out.append(clean)
    return out
