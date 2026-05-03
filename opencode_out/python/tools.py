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


def _android_webview_fetch(url: str) -> str | None:
    try:
        from com.opencode.app import MainActivity
        activity = MainActivity.instance
        if activity is None:
            return None
        html = activity.fetchUrlSync(url)
        if html and len(html) > 200:
            return html
    except Exception:
        pass
    return None


def websearch(query: str, num_results: int = 8) -> str:
    import urllib.parse
    from html.parser import HTMLParser

    class DDGParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.results = []
            self._cur = None
            self._capture = None
            self._depth = 0
            self._result_depth = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            cls = attrs.get('class', '')
            if tag == 'div':
                self._depth += 1
                if 'result__body' in cls:
                    self._cur = {'title': '', 'url': '', 'snippet': ''}
                    self._result_depth = self._depth
                    return
            if self._cur is None:
                return
            if tag == 'a' and 'result__a' in cls:
                self._capture = 'title'
            elif tag == 'span' and 'result__url' in cls:
                self._capture = 'url'
            elif tag == 'a' and 'result__snippet' in cls:
                self._capture = 'snippet'

        def handle_endtag(self, tag):
            if tag in ('a', 'span'):
                self._capture = None
            if tag == 'div':
                if self._cur and self._result_depth == self._depth:
                    if self._cur.get('title'):
                        self.results.append(self._cur)
                    self._cur = None
                    self._result_depth = None
                    self._capture = None
                self._depth -= 1

        def handle_data(self, data):
            if self._capture and self._cur is not None:
                self._cur[self._capture] += data

    def _parse_and_format(html_text: str) -> str | None:
        parser = DDGParser()
        parser.feed(html_text)
        results = parser.results[:num_results]
        if not results:
            return None
        lines = [f"Search results for: **{query}**\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **{r['title'].strip()}**")
            url = r['url'].strip()
            if url:
                lines.append(f"   {url}")
            snippet = r['snippet'].strip()
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")
        return "\n".join(lines)

    encoded = urllib.parse.quote(query)
    html = _android_webview_fetch(f"https://html.duckduckgo.com/html/?q={encoded}")
    if html:
        result = _parse_and_format(html)
        if result:
            return result

    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            timeout=30,
        )
        if resp.status_code == 200:
            result = _parse_and_format(resp.text)
            if result:
                return result
    except Exception:
        pass

    return f"No results found for: {query}"


def webfetch(url: str) -> str:
    html = _android_webview_fetch(url)
    if html:
        return strip_html(html)[:30000]

    try:
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers={"Accept": "text/plain", "User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        if resp.status_code == 200 and resp.text.strip():
            return resp.text.strip()[:30000]
    except Exception:
        pass

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=30,
        )
        return strip_html(resp.text)[:30000]
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


def tool_grep(pattern: str, path: str = None, include: str = None) -> str:
    if not state.working_dir:
        return "No working directory set. Use /set_working_dir to set it first."
    base = resolve_path(path) if path else state.working_dir
    if not is_within_dir(base, state.working_dir):
        return f"Error: Path '{path}' is outside working directory"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex: {e}"
    results = []
    try:
        for root, _dirs, files in os.walk(base):
            if not is_within_dir(root, state.working_dir):
                continue
            for name in files:
                if include and not fnmatch.fnmatch(name, include):
                    continue
                fpath = os.path.join(root, name)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = os.path.relpath(fpath, base)
                                results.append(f"{rel}:{i}: {line.rstrip()}")
                                if len(results) >= 100:
                                    break
                except Exception:
                    pass
            if len(results) >= 100:
                break
    except Exception as e:
        return f"Grep error: {e}"
    if not results:
        return f"No matches for '{pattern}'"
    return "Matches:\n" + "\n".join(results[:100])


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
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        if replaceAll:
            new_content = content.replace(oldString, newString)
            count = content.count(oldString)
        else:
            if oldString not in content:
                return "Text not found in file"
            new_content = content.replace(oldString, newString, 1)
            count = 1
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"Replaced {count} occurrence(s) in {filePath}"
    except Exception as e:
        return f"Edit error: {e}"


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
# TOOLS list  (OpenAI-compatible tool specs)
# ══════════════════════════════════════════════════════════════════════

TOOLS: list = [
    agents_mod.SPAWN_AGENT_TOOL,
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
            "name": "grep",
            "description": "Search file contents for a pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path":    {"type": "string", "description": "Directory or file to search (defaults to working directory)"},
                    "include": {"type": "string", "description": "File pattern to include (*.js, *.py, etc.)"},
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
]


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
        if name == "web_search":
            result = websearch(args.get("query", ""), args.get("num_results", 8))
        elif name == "web_fetch":
            result = webfetch(args.get("url", ""))
        elif name == "glob":
            result = tool_glob(args.get("pattern", "*"), args.get("path"))
        elif name == "grep":
            result = tool_grep(args.get("pattern", ""), args.get("path"), args.get("include"))
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
