"""
script_session.py — Per-provider session state.

Tracks whether a WebView is loaded for a given script ID,
handles model/URL switching, and counts errors for re-auth detection.
"""

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionState:
    script_id:    str
    url:          str
    port:         int
    loaded:       bool    = False
    error_count:  int     = 0
    max_errors:   int     = 5


class ScriptSessionManager:
    """
    Thread-safe registry of active script sessions.
    One session per script_id (= provider folder name).
    """

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def get_or_create(self, script_id: str, url: str, port: int) -> SessionState:
        with self._lock:
            existing = self._sessions.get(script_id)
            if existing:
                if existing.url != url:
                    # URL changed → invalidate
                    del self._sessions[script_id]
                else:
                    return existing
            s = SessionState(script_id=script_id, url=url, port=port)
            self._sessions[script_id] = s
            return s

    def is_loaded(self, script_id: str) -> bool:
        with self._lock:
            s = self._sessions.get(script_id)
            return bool(s and s.loaded)

    def mark_loaded(self, script_id: str):
        with self._lock:
            s = self._sessions.get(script_id)
            if s:
                s.loaded = True

    def record_error(self, script_id: str) -> bool:
        """Returns True if error threshold reached (needs re-auth)."""
        with self._lock:
            s = self._sessions.get(script_id)
            if not s:
                return False
            s.error_count += 1
            return s.error_count >= s.max_errors

    def reset_errors(self, script_id: str):
        with self._lock:
            s = self._sessions.get(script_id)
            if s:
                s.error_count = 0

    def invalidate(self, script_id: str):
        with self._lock:
            self._sessions.pop(script_id, None)

    def all_sessions(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id":          s.script_id,
                    "url":         s.url,
                    "port":        s.port,
                    "loaded":      s.loaded,
                    "error_count": s.error_count,
                }
                for s in self._sessions.values()
            ]


# Singleton
session_manager = ScriptSessionManager()
