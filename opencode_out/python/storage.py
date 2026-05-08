"""
Storage and path utilities: opencode dir resolution, chat file
paths, and working-directory helpers.
"""

import os
import re


def get_opencode_dir() -> str:
    # This folder is persistent across app updates.
    # Never delete or recreate it — existing providers/chats are preserved.
    possible_paths = [
        "/data/data/com.opencode.app/files/storage_dir.txt",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage_dir.txt"),
    ]
    for storage_file in possible_paths:
        if os.path.isfile(storage_file):
            try:
                with open(storage_file, "r") as f:
                    external_path = f.read().strip()
                if external_path:
                    os.makedirs(external_path, exist_ok=True)
                    if os.path.isdir(external_path):
                        return external_path
            except Exception:
                pass
    base = "/storage/emulated/0"
    if not os.path.isdir(base):
        base = "/sdcard"
    d = os.path.join(base, "opencode")
    os.makedirs(d, exist_ok=True)
    return d


def chats_index_file() -> str:
    return os.path.join(get_opencode_dir(), "index.json")


def chat_file(chat_id: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', chat_id)
    return os.path.join(get_opencode_dir(), f"{safe}.json")


def resolve_path(path: str, cwd: str = None) -> str | None:
    """Resolve *path* relative to *cwd* (defaults to the current working dir)."""
    import python.state as state
    if not cwd:
        cwd = state.working_dir
    if not cwd:
        return None
    path = path.strip()
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(cwd, path))


def is_within_dir(path: str, dir_path: str) -> bool:
    abs_path = os.path.abspath(path)
    abs_dir  = os.path.abspath(dir_path)
    return abs_path.startswith(abs_dir + os.sep) or abs_path == abs_dir
