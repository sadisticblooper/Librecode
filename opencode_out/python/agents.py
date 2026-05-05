"""
Agent profile and system-prompt loading.

Exports:
    SYSTEM_PROMPT_BASE  – base system prompt string
    AGENT_PROFILES      – dict[agent_id, profile_dict]
    SPAWN_AGENT_TOOL    – OpenAI-tool-spec dict for the spawn_agent tool
    make_spawn_agent_tool()  – (re)build SPAWN_AGENT_TOOL from current profiles
"""

import os
import json
import shutil

# ── Paths ──────────────────────────────────────────────────────────────
# Bundled prompts live inside opencode_out/prompts/ (two levels up from this file)
_BUNDLED_PROMPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "prompts",
)

# Bundled providers live inside opencode_out/python/providers/ (sibling of this file)
_BUNDLED_PROVIDERS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "providers",
)

# ── Fallback constants ─────────────────────────────────────────────────
_DEFAULT_SYSTEM_MD = (
    "You are a coding assistant running on a mobile Android app called OpenCode.\n\n"
    "Be direct and concise. No unnecessary preamble, no enthusiasm theater, no filler phrases.\n"
    "For simple questions, answer in 1-2 sentences. Only elaborate when the task genuinely requires it.\n"
    "Never start responses with affirmations like \"Sure!\", \"Great!\", \"Of course!\", \"Absolutely!\" etc."
)

_DEFAULT_INDEX_JSON = [
    {
        "id": "build",
        "name": "build",
        "description": "Full access — code, write, run commands",
        "file": "build.md",
        "no_tools": False,
        "denied_tools": [],
    },
]


# ── Helpers ────────────────────────────────────────────────────────────

def _copy_missing_prompts(src: str, dst: str) -> None:
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            target = os.path.join(target_root, name)
            if not os.path.exists(target):
                shutil.copy2(os.path.join(root, name), target)


def get_prompts_dir() -> str:
    from python.storage import get_opencode_dir
    local_prompts = os.path.join(get_opencode_dir(), "prompts")
    try:
        _copy_missing_prompts(_BUNDLED_PROMPTS, local_prompts)
    except Exception:
        pass
    if (
        os.path.isfile(os.path.join(local_prompts, "system.md"))
        or os.path.isfile(os.path.join(local_prompts, "agents", "index.json"))
    ):
        return local_prompts
    return _BUNDLED_PROMPTS


_BUNDLED_PROVIDER_NAMES = ["free", "gemini", "ollama"]


def _copy_providers(dst: str) -> None:
    import pkgutil
    os.makedirs(dst, exist_ok=True)
    for name in _BUNDLED_PROVIDER_NAMES:
        target = os.path.join(dst, f"{name}.py")
        if os.path.exists(target):
            continue
        data = pkgutil.get_data("python.providers", f"{name}.py")
        if data is not None:
            with open(target, "wb") as f:
                f.write(data)


def get_providers_dir() -> str:
    from python.storage import get_opencode_dir
    local_providers = os.path.join(get_opencode_dir(), "providers")
    _copy_providers(local_providers)
    return local_providers


def _load_system_prompt() -> str:
    path = os.path.join(get_prompts_dir(), "system.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return _DEFAULT_SYSTEM_MD.strip()


def _load_agent_index(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def _load_agents() -> dict:
    agents_dir  = os.path.join(get_prompts_dir(), "agents")
    index_path  = os.path.join(agents_dir, "index.json")
    entries     = _load_agent_index(index_path)
    if not entries:
        entries = list(_DEFAULT_INDEX_JSON)

    bundled_index = os.path.join(_BUNDLED_PROMPTS, "agents", "index.json")
    if os.path.abspath(index_path) != os.path.abspath(bundled_index):
        existing_ids = {e.get("id") for e in entries}
        for entry in _load_agent_index(bundled_index):
            if entry.get("id") and entry.get("id") not in existing_ids:
                entries.append(entry)
                existing_ids.add(entry.get("id"))

    profiles: dict = {}
    for entry in entries:
        agent_id = entry.get("id", "")
        if not agent_id:
            continue
        md_file         = os.path.join(agents_dir, entry.get("file", f"{agent_id}.md"))
        bundled_md_file = os.path.join(_BUNDLED_PROMPTS, "agents", entry.get("file", f"{agent_id}.md"))
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                system_suffix = f.read().strip()
        except Exception:
            try:
                with open(bundled_md_file, "r", encoding="utf-8") as f:
                    system_suffix = f.read().strip()
            except Exception:
                system_suffix = f"You are in {agent_id.upper()} mode."

        profiles[agent_id] = {
            "name":          entry.get("name", agent_id),
            "description":   entry.get("description", ""),
            "system_suffix": system_suffix,
            "no_tools":      entry.get("no_tools", False),
            "denied_tools":  entry.get("denied_tools", []),
        }

    return profiles


# ── Module-level globals (mutated by reload_agents in tools.py) ────────

SYSTEM_PROMPT_BASE: str  = _load_system_prompt()
AGENT_PROFILES: dict     = _load_agents()
get_providers_dir()


def make_spawn_agent_tool() -> dict:
    """Build the spawn_agent tool spec from the current AGENT_PROFILES."""
    agent_lines = [
        f"- {aid}: {profile.get('description', '')}".rstrip()
        for aid, profile in sorted(AGENT_PROFILES.items())
    ]
    agents = sorted(AGENT_PROFILES.keys()) or ["build"]
    return {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "Delegate a self-contained task to a specialized subagent. "
                "The subagent runs to completion and returns its full output. "
                "Use this to parallelize work, delegate read-only analysis before writing, "
                "or break large tasks into focused subtasks.\n\n"
                "Agents:\n" + "\n".join(agent_lines)
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "enum": agents,
                        "description": "Which agent profile to run",
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "The complete task description. Be specific - the subagent has no "
                            "context from the main conversation. Include file paths, exact "
                            "requirements, and expected output format."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Optional extra context to prepend to the task, such as a relevant "
                            "file snippet, error message, or prior subagent output."
                        ),
                    },
                },
                "required": ["agent_id", "task"],
            },
        },
    }


SPAWN_AGENT_TOOL: dict = make_spawn_agent_tool()
