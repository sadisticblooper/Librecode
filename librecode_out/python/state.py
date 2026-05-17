"""
Mutable application state shared across modules.
Import this module and reference attributes directly so updates
made by one module are visible to all others:

    import python.state as state
    state.working_dir = "/new/path"
"""

working_dir  = ""
working_dirs = []

# chat_id -> list of rich turn objects sent to the API (may be compacted)
chat_histories: dict = {}
# chat_id -> full uncompacted history for display (never truncated)
chat_display_histories: dict = {}
# chat_id -> previous compaction summary string
chat_summaries: dict = {}
# chat_id -> sequence counter for turn ID generation
chat_msg_counts: dict = {}
# chat_id -> list of todo items [{id, content, status}]
todo_lists: dict = {}
# chat_id -> {"input": int, "output": int}  (tokens for that chat only)
chat_token_counts: dict = {}

current_chat_id = None
