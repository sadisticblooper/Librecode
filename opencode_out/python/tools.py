"""
Tool implementations and the TOOLS spec list used by the LLM.

Sections:
  - Web utilities        (strip_html, websearch, webfetch)
  - Native binary setup  (_get_toybox_path, _setup_cli_path, …)
  - File tools           (tool_glob, tool_grep, tool_read, tool_write, tool_edit)
  - GitHub tool          (tool_github_walk)
  - Binary tools         (tool_rg, tool_fd)
  - Exec tools           (tool_python_exec, tool_shell)
  - TOOLS list           (OpenAI-compatible tool specs)
  - Helpers              (get_tools_for_agent, reload_agents, run_tool)
"""

import os
import re
import json
import fnmatch
import glob as glob_module
import requests

import python.agents as agents_mod
import python.state as state
from python.storage import resolve_path, is_within_dir


# ══════════════════════════════════════════════════════════════════════
# Web utilities
# ══════════════════════════════════════════════════════════════════════

def strip_html(html: str) -> str:
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>',  '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<!--.*?-->',               '', html, flags=re.DOTALL)
    html = re.sub(r'<br\s*/?>',               '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<p[^>]*>',               '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<h[1-6][^>]*>',       '\n## ', html, flags=re.IGNORECASE)
    html = re.sub(r'<li[^>]*>',            '\n- ', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&')
    html = html.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    html = re.sub(r'\n{3,}', '\n\n', html)
    html = re.sub(r'[ \t]+', ' ', html)
    return html.strip()


def websearch(query: str, num_results: int = 8) -> str:
    """Search the web using Exa's free MCP endpoint (no API key needed)."""
    import json as _json

    exa_url = "https://mcp.exa.ai/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "type": "auto",
                "numResults": num_results,
                "livecrawl": "fallback",
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "opencode",
    }

    try:
        resp = requests.post(exa_url, json=payload, headers=headers, timeout=25)
        resp.raise_for_status()
        body = resp.text

        # Parse SSE response
        for line in body.split("\n"):
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            try:
                data = _json.loads(data_str)
                content = data.get("result", {}).get("content", [])
                if content and content[0].get("text"):
                    return content[0]["text"]
            except _json.JSONDecodeError:
                continue

        return f"No search results found for: {query}"
    except Exception as e:
        return f"Search error: {e}"


def webfetch(url: str) -> str:
    """Fetch webpage content and return as text."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            return strip_html(resp.text)[:30000]
        return resp.text[:30000]
    except Exception as e:
        return f"Fetch error: {e}"


# ══════════════════════════════════════════════════════════════════════
# Native binary helpers (Android-specific paths)
# ══════════════════════════════════════════════════════════════════════

def _get_toybox_path() -> str | None:
    candidates = [
        "/data/data/com.opencode.app/files/toybox_path.txt",
        "/data/user/0/com.opencode.app/files/toybox_path.txt",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                with open(p) as f:
                    path = f.read().strip()
                if path and os.path.isfile(path) and os.access(path, os.X_OK):
                    return path
            except Exception:
                pass
    for p in ["/system/bin/toybox", "/usr/bin/toybox", "/bin/toybox"]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _get_native_lib_dir() -> str:
    for p in [
        "/data/data/com.opencode.app/files/native_lib_dir.txt",
        "/data/user/0/com.opencode.app/files/native_lib_dir.txt",
    ]:
        if os.path.isfile(p):
            try:
                with open(p) as f:
                    path = f.read().strip()
                if path and os.path.isdir(path):
                    return path
            except Exception:
                pass
    return ""


def _app_files_dir() -> str:
    for p in ["/data/data/com.opencode.app/files", "/data/user/0/com.opencode.app/files"]:
        if os.path.isdir(p):
            return p
    return os.path.dirname(os.path.abspath(__file__))


def _native_tool_path(binary: str) -> str:
    native_dir = _get_native_lib_dir()
    path = os.path.join(native_dir, binary) if native_dir else ""
    if path and os.path.isfile(path):
        try:
            os.chmod(path, 0o755)
        except Exception:
            pass
        return path
    return ""


def _setup_cli_path() -> dict:
    native_dir = _get_native_lib_dir()
    bin_dir = os.path.join(_app_files_dir(), "bin")
    os.makedirs(bin_dir, exist_ok=True)

    tools_map = {"rg": "librg-bin.so", "fd": "libfd-bin.so"}
    toybox = _get_toybox_path()
    if toybox:
        tools_map["toybox"] = os.path.basename(toybox)

    for stale in ["jq"]:
        stale_path = os.path.join(bin_dir, stale)
        if os.path.exists(stale_path):
            try:
                os.remove(stale_path)
            except Exception:
                pass

    for name, binary in tools_map.items():
        real = toybox if name == "toybox" else os.path.join(native_dir, binary)
        wrapper = os.path.join(bin_dir, name)
        if not real or not os.path.isfile(real):
            continue
        try:
            os.chmod(real, 0o755)
        except Exception:
            pass
        try:
            with open(wrapper, "w", encoding="utf-8") as f:
                f.write(f'#!/system/bin/sh\nexec "{real}" "$@"\n')
            os.chmod(wrapper, 0o755)
        except Exception:
            pass

    env = os.environ.copy()
    path_parts = [bin_dir, native_dir, env.get("PATH", ""), "/system/bin", "/system/xbin"]
    env["PATH"] = ":".join(p for p in path_parts if p)
    return env


def _tool_work_dirs(path: str = None) -> tuple:
    """Return (dirs, error_string). error_string is None on success."""
    dirs = state.working_dirs if state.working_dirs else ([state.working_dir] if state.working_dir else [])
    if path:
        base = resolve_path(path, dirs[0] if dirs else None) if dirs else path
        if not base or not os.path.exists(base):
            return [], f"Error: Path not found: {path}"
        if dirs and not any(is_within_dir(base, d) for d in dirs):
            return [], f"Error: Path '{path}' is outside all working directories"
        return [base], None
    if not dirs:
        return [], "No working directory set."
    return dirs, None


def _run_direct_binary(binary: str, args: list, cwd: str = None, timeout: int = 30) -> str:
    import subprocess
    exe = _native_tool_path(binary)
    if not exe:
        return f"Error: bundled binary not available: {binary}"
    try:
        result = subprocess.run(
            [exe] + args,
            cwd=cwd if cwd and os.path.isdir(cwd) else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if not output.strip():
            return f"(exit code {result.returncode}, no output)"
        if len(output) > 20000:
            output = output[:20000] + "\n... (truncated)"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout} seconds"
    except Exception as e:
        return f"Error running {binary}: {e}"


# ══════════════════════════════════════════════════════════════════════
# File tools
# ══════════════════════════════════════════════════════════════════════

def tool_glob(pattern: str, path: str = None) -> str:
    dirs = state.working_dirs if state.working_dirs else ([state.working_dir] if state.working_dir else [])
    if not dirs:
        return "No working directory set."
    base_dir = dirs[0]
    base = resolve_path(path, base_dir) if path else base_dir
    if not any(is_within_dir(base, d) for d in dirs):
        return f"Error: Path '{path}' is outside all working directories"
    try:
        full_pattern = os.path.join(base, pattern)
        matches = glob_module.glob(full_pattern, recursive=True)
        matches = [m for m in matches if any(is_within_dir(m, d) for d in dirs)]
        if not matches:
            return f"No files matching '{pattern}'"
        rel_matches = [os.path.relpath(m, base) for m in matches[:100]]
        return "Found files:\n" + "\n".join(rel_matches)
    except Exception as e:
        return f"Glob error: {e}"



def tool_read(filePath: str, offset: int = None, limit: int = None) -> str:
    if not state.working_dir:
        return "No working directory set. Use /set_working_dir to set it first."
    full_path = resolve_path(filePath)
    if not full_path or not is_within_dir(full_path, state.working_dir):
        return f"Error: Path '{filePath}' is outside working directory"
    if not os.path.isfile(full_path):
        return f"Error: File not found: {filePath}"
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        start = (offset - 1) if offset else 0
        end   = len(lines) if limit is None else start + limit
        lines = lines[start:end]
        content = "".join(lines)
        prefix  = f"Lines {start + 1}-{end}:\n" if offset or limit else ""
        if len(content) > 50000:
            content = content[:50000] + "\n... (truncated at 50000 chars)"
        return prefix + content
    except Exception as e:
        return f"Read error: {e}"


def tool_write(content: str, filePath: str) -> str:
    if not state.working_dir:
        return "No working directory set. Use /set_working_dir to set it first."
    full_path = resolve_path(filePath)
    if not full_path or not is_within_dir(full_path, state.working_dir):
        return f"Error: Path '{filePath}' is outside working directory"
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Written to {filePath} ({len(content)} chars)"
    except Exception as e:
        return f"Write error: {e}"


def tool_edit(filePath: str, oldString: str, newString: str, replaceAll: bool = False) -> str:
    if not state.working_dir:
        return "No working directory set. Use /set_working_dir to set it first."
    full_path = resolve_path(filePath)
    if not full_path or not is_within_dir(full_path, state.working_dir):
        return f"Error: Path '{filePath}' is outside working directory"
    if not os.path.isfile(full_path):
        return f"Error: File not found: {filePath}"
    if oldString == newString:
        return "Error: oldString and newString are identical"
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Try matching strategies in order of specificity
        search = _find_match(content, oldString)
        if search is None:
            return "Error: Could not find oldString in the file. It must match exactly including whitespace."

        if replaceAll:
            new_content = content.replace(search, newString)
            count = content.count(search)
        else:
            idx = content.find(search)
            last = content.rfind(search)
            if idx != last:
                return "Error: Found multiple matches. Provide more surrounding context to make the match unique."
            new_content = content[:idx] + newString + content[idx + len(search):]
            count = 1

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        import difflib
        diff_lines = difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f'a/{filePath}',
            tofile=f'b/{filePath}',
            n=3,
        )
        diff_text = ''.join(diff_lines)
        if diff_text:
            return f"Replaced {count} occurrence(s) in {filePath}\n\n<<<DIFF>>>\n{diff_text}"
        return f"Replaced {count} occurrence(s) in {filePath}"
    except Exception as e:
        return f"Edit error: {e}"


def _find_match(content: str, old: str) -> str | None:
    """Try multiple matching strategies. Returns the matched string or None."""

    # 1. Exact match
    if old in content:
        return old

    # 2. Line-trimmed match
    result = _line_trimmed_match(content, old)
    if result:
        return result

    # 3. Indentation-flexible match
    result = _indent_flexible_match(content, old)
    if result:
        return result

    # 4. Whitespace-normalized match
    result = _whitespace_normalized_match(content, old)
    if result:
        return result

    return None


def _line_trimmed_match(content: str, old: str) -> str | None:
    """Match ignoring leading/trailing whitespace on each line."""
    content_lines = content.split('\n')
    old_lines = old.split('\n')
    if old_lines and old_lines[-1] == '':
        old_lines.pop()

    for i in range(len(content_lines) - len(old_lines) + 1):
        match = True
        for j in range(len(old_lines)):
            if content_lines[i + j].strip() != old_lines[j].strip():
                match = False
                break
        if match:
            start = sum(len(content_lines[k]) + 1 for k in range(i))
            end = start + sum(len(content_lines[i + k]) for k in range(len(old_lines)))
            end += len(old_lines) - 1  # newlines between lines
            return content[start:end]
    return None


def _indent_flexible_match(content: str, old: str) -> str | None:
    """Match ignoring indentation differences."""
    def remove_indent(text):
        lines = text.split('\n')
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return text
        min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
        return '\n'.join(l[min_indent:] if l.strip() else l for l in lines)

    content_lines = content.split('\n')
    old_lines = old.split('\n')
    if old_lines and old_lines[-1] == '':
        old_lines.pop()

    normalized_old = remove_indent(old)

    for i in range(len(content_lines) - len(old_lines) + 1):
        block = '\n'.join(content_lines[i:i + len(old_lines)])
        if remove_indent(block) == normalized_old:
            return block
    return None


def _whitespace_normalized_match(content: str, old: str) -> str | None:
    """Match with normalized whitespace (collapse multiple spaces)."""
    import re as _re
    def norm(text):
        return _re.sub(r'\s+', ' ', text).strip()

    if norm(old) == norm(content):
        return content

    content_lines = content.split('\n')
    old_lines = old.split('\n')
    if old_lines and old_lines[-1] == '':
        old_lines.pop()

    for i in range(len(content_lines) - len(old_lines) + 1):
        block = '\n'.join(content_lines[i:i + len(old_lines)])
        if norm(block) == norm(old):
            return block
    return None


def tool_diff(fileA: str, fileB: str, context: int = 3) -> str:
    import difflib
    if not state.working_dir:
        return "No working directory set. Use /set_working_dir to set it first."
    pathA = resolve_path(fileA)
    pathB = resolve_path(fileB)
    if not pathA or not os.path.isfile(pathA):
        return f"Error: File not found: {fileA}"
    if not pathB or not os.path.isfile(pathB):
        return f"Error: File not found: {fileB}"
    try:
        with open(pathA, 'r', encoding='utf-8', errors='ignore') as f:
            linesA = f.readlines()
        with open(pathB, 'r', encoding='utf-8', errors='ignore') as f:
            linesB = f.readlines()
        diff = list(difflib.unified_diff(linesA, linesB, fromfile=f'a/{fileA}', tofile=f'b/{fileB}', n=context))
        if not diff:
            return f"No differences between {fileA} and {fileB}\n\n<<<DIFF>>>\n"
        return f"Diff: {fileA} vs {fileB}\n\n<<<DIFF>>>\n" + ''.join(diff)
    except Exception as e:
        return f"Diff error: {e}"


# ══════════════════════════════════════════════════════════════════════
# GitHub tool
# ══════════════════════════════════════════════════════════════════════

def tool_github_walk(action: str, repo: str, file_path: str = None, branch: str = None) -> str:
    headers = {"User-Agent": "opencode-app", "Accept": "application/vnd.github+json"}

    if not branch:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=15)
            if r.status_code == 404:
                return f"Repo not found: {repo}"
            branch = r.json().get("default_branch", "main")
        except Exception as e:
            return f"GitHub API error: {e}"

    if action == "tree":
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                headers=headers, timeout=20,
            )
            if r.status_code != 200:
                return f"Failed to get tree: {r.status_code} {r.text[:200]}"
            data      = r.json()
            items     = data.get("tree", [])
            truncated = data.get("truncated", False)
            files     = [item["path"] for item in items if item["type"] == "blob"]
            total     = len(files)
            MAX_FILES = 400
            capped    = total > MAX_FILES
            if capped:
                files = files[:MAX_FILES]
            lines = [f"# {repo} @ {branch}  ({total} files total)\n"]
            lines += files
            if capped:
                lines.append(f"\n... showing {MAX_FILES}/{total} files. Use action='read' with a specific file_path to read any file.")
            if truncated:
                lines.append("GitHub truncated the tree (repo is very large). Results may be incomplete.")
            return "\n".join(lines)
        except Exception as e:
            return f"GitHub tree error: {e}"

    elif action == "read":
        if not file_path:
            return "file_path is required for action='read'"
        try:
            raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
            r = requests.get(raw_url, headers={"User-Agent": "opencode-app"}, timeout=20)
            if r.status_code == 404:
                return f"File not found: {file_path} on branch {branch}"
            if r.status_code != 200:
                return f"Failed to read file: {r.status_code}"
            content = r.text
            if len(content) > 20000:
                content = content[:20000] + f"\n\n... (truncated, {len(r.text)} chars total)"
            return f"# {repo}/{file_path}\n\n{content}"
        except Exception as e:
            return f"GitHub read error: {e}"

    return f"Unknown action: {action}"


# ══════════════════════════════════════════════════════════════════════
# Binary tools (rg / fd)
# ══════════════════════════════════════════════════════════════════════

def tool_rg(
    pattern: str,
    path: str = None,
    glob: str = None,
    case_insensitive: bool = False,
    context: int = 0,
    max_results: int = 200,
) -> str:
    targets, err = _tool_work_dirs(path)
    if err:
        return err
    args = ["--line-number", "--color", "never"]
    if case_insensitive:
        args.append("--ignore-case")
    try:
        context = int(context or 0)
    except Exception:
        context = 0
    if context > 0:
        args += ["--context", str(min(context, 20))]
    if glob:
        args += ["--glob", str(glob)]
    args += [pattern] + targets
    output = _run_direct_binary("librg-bin.so", args, cwd=targets[0])
    lines  = output.splitlines()
    try:
        max_results = int(max_results or 200)
    except Exception:
        max_results = 200
    if max_results > 0 and len(lines) > max_results:
        return "\n".join(lines[:max_results]) + f"\n... ({len(lines) - max_results} more lines)"
    return output


def tool_fd(
    pattern: str = "",
    path: str = None,
    extension: str = None,
    type: str = None,
    hidden: bool = False,
    max_results: int = 200,
) -> str:
    targets, err = _tool_work_dirs(path)
    if err:
        return err
    args = ["--color", "never"]
    if hidden:
        args.append("--hidden")
    if extension:
        args += ["--extension", str(extension).lstrip(".")]
    if type in ("file", "f"):
        args += ["--type", "file"]
    elif type in ("directory", "dir", "d"):
        args += ["--type", "directory"]
    args.append(pattern or "")
    args += targets
    output = _run_direct_binary("libfd-bin.so", args, cwd=targets[0])
    lines  = output.splitlines()
    try:
        max_results = int(max_results or 200)
    except Exception:
        max_results = 200
    if max_results > 0 and len(lines) > max_results:
        return "\n".join(lines[:max_results]) + f"\n... ({len(lines) - max_results} more lines)"
    return output


# ══════════════════════════════════════════════════════════════════════
# Exec tools
# ══════════════════════════════════════════════════════════════════════

def tool_python_exec(code: str, cwd: str = None) -> str:
    import io
    import traceback
    import contextlib

    run_cwd = cwd or state.working_dir or None
    if run_cwd and not os.path.isdir(run_cwd):
        run_cwd = None

    stdout  = io.StringIO()
    stderr  = io.StringIO()
    old_cwd = os.getcwd()
    scope   = {
        "__name__":   "__tool__",
        "os":         os,
        "json":       json,
        "re":         re,
        "working_dir":  state.working_dir,
        "working_dirs": state.working_dirs,
    }
    try:
        if run_cwd:
            os.chdir(run_cwd)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = eval(compile(code, "<python_exec>", "eval"), scope, scope)
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                exec(compile(code, "<python_exec>", "exec"), scope, scope)
    except Exception:
        stderr.write(traceback.format_exc())
    finally:
        try:
            os.chdir(old_cwd)
        except Exception:
            pass

    output = stdout.getvalue()
    err    = stderr.getvalue()
    if err:
        output += ("\n" if output else "") + err
    output = output.strip()
    if not output:
        return "(python completed, no output)"
    if len(output) > 20000:
        output = output[:20000] + "\n... (truncated)"
    return output


def tool_shell(command: str, cwd: str = None) -> str:
    import subprocess

    toybox = _get_toybox_path()
    if not toybox:
        return "Error: toybox binary not available."

    run_cwd = cwd or state.working_dir or None
    if run_cwd and not os.path.isdir(run_cwd):
        run_cwd = None

    command = command.strip()
    if not command:
        return "Empty command."

    try:
        result = subprocess.run(
            ["/system/bin/sh", "-c", command],
            cwd=run_cwd,
            env=_setup_cli_path(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if not output.strip():
            return f"(exit code {result.returncode}, no output)"
        if len(output) > 20000:
            output = output[:20000] + "\n... (truncated)"
        return output
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds"
    except Exception as e:
        return f"Shell error: {e}"


# ══════════════════════════════════════════════════════════════════════
# Todo tools
# ══════════════════════════════════════════════════════════════════════

def tool_todo_write(todos: list) -> str:
    """Replace the current todo list with the provided items."""
    chat_id = state.current_chat_id or "default"
    if not isinstance(todos, list):
        return "Error: todos must be a list of {id, content, status} objects"
    validated = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        validated.append({
            "id":      str(item.get("id", len(validated) + 1)),
            "content": str(item.get("content", "")),
            "status":  item.get("status", "pending") if item.get("status") in ("pending", "in_progress", "completed") else "pending",
        })
    state.todo_lists[chat_id] = validated
    lines = []
    for t in validated:
        icon = {"pending": "○", "in_progress": "◑", "completed": "✓"}.get(t["status"], "○")
        lines.append(f"{icon} [{t['id']}] {t['content']}")
    return "Todo list updated:\n" + "\n".join(lines) if lines else "Todo list cleared."


def tool_todo_read() -> str:
    """Return the current todo list."""
    chat_id = state.current_chat_id or "default"
    todos = state.todo_lists.get(chat_id, [])
    if not todos:
        return "No todos."
    lines = []
    for t in todos:
        icon = {"pending": "○", "in_progress": "◑", "completed": "✓"}.get(t["status"], "○")
        lines.append(f"{icon} [{t['id']}] {t['content']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# TOOLS list  (OpenAI-compatible tool specs)
# ══════════════════════════════════════════════════════════════════════

TOOLS: list = [
    agents_mod.SPAWN_AGENT_TOOL,
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Write or update the task todo list. Use this to plan tasks, track progress, and maintain "
                "visibility into what you're doing. Call this BEFORE starting any multi-step task to create "
                "the plan, then update statuses as you work (pending → in_progress → completed). "
                "Mark items completed immediately when done — do not batch updates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Full todo list. Replaces the current list entirely.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id":      {"type": "string",  "description": "Unique short ID, e.g. '1', '2'"},
                                "content": {"type": "string",  "description": "Task description"},
                                "status":  {"type": "string",  "description": "One of: pending, in_progress, completed"},
                            },
                            "required": ["id", "content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Read the current todo list. Use this to check what's pending before starting work or after completing a step.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information, news, and facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string",  "description": "The search query"},
                    "num_results": {"type": "integer", "description": "Number of results to return (default 8)", "default": 8},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and read the text content of a webpage given its URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a pattern in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g., **/*.js)"},
                    "path":    {"type": "string", "description": "Base directory (defaults to working directory)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rg",
            "description": "Run bundled ripgrep directly without shell wrapping. Use this for fast regex text search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":          {"type": "string",  "description": "Regex pattern to search for"},
                    "path":             {"type": "string",  "description": "Directory or file to search (defaults to all working directories)"},
                    "glob":             {"type": "string",  "description": "Optional include/exclude glob, e.g. '*.py' or '!*.min.js'"},
                    "case_insensitive": {"type": "boolean", "description": "Use case-insensitive search", "default": False},
                    "context":          {"type": "integer", "description": "Context lines before and after matches", "default": 0},
                    "max_results":      {"type": "integer", "description": "Maximum output lines to return", "default": 200},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fd",
            "description": "Run bundled fd directly without shell wrapping. Use this for fast file and directory search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",  "description": "Search pattern (defaults to all files)", "default": ""},
                    "path":        {"type": "string",  "description": "Directory to search (defaults to all working directories)"},
                    "extension":   {"type": "string",  "description": "Optional file extension filter, e.g. py, js, kt"},
                    "type":        {"type": "string",  "description": "Optional result type: file or directory"},
                    "hidden":      {"type": "boolean", "description": "Include hidden files", "default": False},
                    "max_results": {"type": "integer", "description": "Maximum output lines to return", "default": 200},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filePath": {"type": "string",  "description": "Path to the file (relative to working directory)"},
                    "offset":   {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
                    "limit":    {"type": "integer", "description": "Maximum number of lines to read"},
                },
                "required": ["filePath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "content":  {"type": "string", "description": "Content to write to the file"},
                    "filePath": {"type": "string", "description": "Path to the file (relative to working directory)"},
                },
                "required": ["content", "filePath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_walk",
            "description": (
                "Explore a GitHub repository without guessing URLs. Use action='tree' to get the full "
                "file/folder structure of any public repo. Use action='read' to get the raw contents "
                "of a specific file. Provide repo as 'owner/repo' (e.g. 'torvalds/linux'). Optionally "
                "specify a branch (defaults to main/master)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action":    {"type": "string", "description": "Use 'tree' to list all files in the repo, 'read' to fetch a specific file's contents"},
                    "repo":      {"type": "string", "description": "GitHub repo in 'owner/repo' format, e.g. 'facebook/react'"},
                    "file_path": {"type": "string", "description": "Path to file within repo (required for action='read'), e.g. 'src/index.js'"},
                    "branch":    {"type": "string", "description": "Branch name (optional, auto-detected if omitted)"},
                },
                "required": ["action", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command through Android sh. Bundled toybox and Android system commands are on PATH. Prefer the rg and fd tools for search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run (e.g., 'ls -la', 'find . -name *.py', 'cat file.txt | grep error')"},
                    "cwd":     {"type": "string", "description": "Working directory override (defaults to project working directory)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": "Execute Python code in the app's Chaquopy Python interpreter. Use this for scripts, JSON processing, file transforms, and calculations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code or expression to execute"},
                    "cwd":  {"type": "string", "description": "Working directory override (defaults to project working directory)"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace a specific string in a file with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filePath":   {"type": "string",  "description": "Path to the file (relative to working directory)"},
                    "oldString":  {"type": "string",  "description": "Text to find and replace"},
                    "newString":  {"type": "string",  "description": "Text to replace it with"},
                    "replaceAll": {"type": "boolean", "description": "Replace all occurrences (default false)", "default": False},
                },
                "required": ["filePath", "oldString", "newString"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diff",
            "description": "Show a unified diff between two files. Use this to compare file versions, review changes, or understand differences between files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fileA":   {"type": "string",  "description": "Path to the first (original) file"},
                    "fileB":   {"type": "string",  "description": "Path to the second (modified) file"},
                    "context": {"type": "integer", "description": "Lines of context around each change (default 3)"},
                },
                "required": ["fileA", "fileB"],
            },
        },
    },
]

from python.browser_tools import BROWSER_OPEN_SPEC as _BROWSER_OPEN_SPEC
TOOLS.append(_BROWSER_OPEN_SPEC)


# ══════════════════════════════════════════════════════════════════════
# Helpers: get_tools_for_agent, reload_agents, run_tool
# ══════════════════════════════════════════════════════════════════════

def get_tools_for_agent(agent_name: str) -> list:
    """Return the filtered TOOLS list for the given agent profile."""
    profiles = agents_mod.AGENT_PROFILES
    fallback = list(profiles.values())[0] if profiles else {}
    profile  = profiles.get(agent_name, fallback)
    if profile.get("no_tools", False):
        return []
    denied = profile.get("denied_tools", [])
    return [t for t in TOOLS if t["function"]["name"] not in denied]


def reload_agents() -> None:
    """Reload agent profiles from disk and update TOOLS in-place."""
    agents_mod.SYSTEM_PROMPT_BASE = agents_mod._load_system_prompt()
    agents_mod.AGENT_PROFILES     = agents_mod._load_agents()
    agents_mod.SPAWN_AGENT_TOOL   = agents_mod.make_spawn_agent_tool()
    for i, tool in enumerate(TOOLS):
        if tool["function"]["name"] == "spawn_agent":
            TOOLS[i] = agents_mod.SPAWN_AGENT_TOOL
            break


def run_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name and return its string result."""
    try:
        if name == "todo_write":
            result = tool_todo_write(args.get("todos", []))
        elif name == "todo_read":
            result = tool_todo_read()
        elif name == "web_search":
            result = websearch(args.get("query", ""), args.get("num_results", 8))
        elif name == "web_fetch":
            result = webfetch(args.get("url", ""))
        elif name == "glob":
            result = tool_glob(args.get("pattern", "*"), args.get("path"))
        elif name == "rg":
            result = tool_rg(
                args.get("pattern", ""),
                args.get("path"),
                args.get("glob"),
                args.get("case_insensitive", False),
                args.get("context", 0),
                args.get("max_results", 200),
            )
        elif name == "fd":
            result = tool_fd(
                args.get("pattern", ""),
                args.get("path"),
                args.get("extension"),
                args.get("type"),
                args.get("hidden", False),
                args.get("max_results", 200),
            )
        elif name == "read":
            result = tool_read(args.get("filePath", ""), args.get("offset"), args.get("limit"))
        elif name == "write":
            result = tool_write(args.get("content", ""), args.get("filePath", ""))
        elif name == "github_walk":
            result = tool_github_walk(
                args.get("action", "tree"),
                args.get("repo", ""),
                args.get("file_path"),
                args.get("branch"),
            )
        elif name == "edit":
            result = tool_edit(
                args.get("filePath", ""),
                args.get("oldString", ""),
                args.get("newString", ""),
                args.get("replaceAll", False),
            )
        elif name == "diff":
            result = tool_diff(
                args.get("fileA", ""),
                args.get("fileB", ""),
                args.get("context", 3),
            )
        elif name == "shell":
            result = tool_shell(args.get("command", ""), args.get("cwd"))
        elif name == "python_exec":
            result = tool_python_exec(args.get("code", ""), args.get("cwd"))
        elif name == "spawn_agent":
            # Lazy import to avoid circular dependency with subagent.py
            from python.subagent import run_subagent
            dirs   = state.working_dirs if state.working_dirs else ([state.working_dir] if state.working_dir else [])
            result = run_subagent(
                agent_id     = args.get("agent_id", "build"),
                task         = args.get("task", ""),
                context      = args.get("context", ""),
                working_dirs = dirs,
            )
        elif name.startswith("browser_") or name == "spawn_browser":
            import python.browser_tools as bt
            dispatch = {
                "browser_open":       lambda: bt.tool_browser_open(args.get("url", "about:blank")),
                "spawn_browser":      lambda: bt.tool_browser_open(args.get("url", "about:blank")),
                "browser_snapshot":   lambda: bt.tool_browser_snapshot(),
                "browser_click":      lambda: bt.tool_browser_click(args.get("uid", "")),
                "browser_fill":       lambda: bt.tool_browser_fill(args.get("uid", ""), args.get("value", "")),
                "browser_navigate":   lambda: bt.tool_browser_navigate(args.get("url", "")),
                "browser_eval":       lambda: bt.tool_browser_eval(args.get("script", "")),
                "browser_wait":       lambda: bt.tool_browser_wait(args.get("text", ""), args.get("timeout_ms", 15000)),
                "browser_screenshot": lambda: bt.tool_browser_screenshot(),
                "browser_cookies":    lambda: bt.tool_browser_cookies(args.get("url", "")),
                "browser_login_cct":  lambda: bt.tool_browser_login_cct(args.get("url", "")),
                "browser_close":      lambda: bt.tool_browser_close(),
                "browser_batch":      lambda: bt.tool_browser_batch(args.get("actions", [])),
                "browser_rg":         lambda: bt.tool_browser_rg(args.get("pattern", ""), args.get("case_insensitive", False)),
                "browser_fd":         lambda: bt.tool_browser_fd(args.get("pattern", ""), args.get("extension")),
            }
            fn = dispatch.get(name)
            if fn is None:
                return f"Error: unknown browser tool '{name}'"
            result = fn()
        else:
            return f"Error: unknown tool '{name}'"
    except Exception as e:
        return f"Error: tool '{name}' raised an exception: {e}"

    if result is None:
        return f"Error: tool '{name}' returned no output. The operation may have failed or the path/query produced no results."
    result = str(result).strip()
    if not result:
        return f"No results: tool '{name}' completed but returned empty output. The search/operation found nothing matching the given parameters."
    return result
