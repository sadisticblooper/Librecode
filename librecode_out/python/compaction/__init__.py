"""
python/compaction package.

Re-exports the public API from compaction.py so callers can write:
    from python.compaction import compact_messages, ...
"""

from python.compaction.compaction import (
    compact_messages,
    build_compacted_messages_for_api,
    estimate_messages_tokens,
    is_overflow,
    split_head_tail,
    generate_summary,
)

__all__ = [
    "compact_messages",
    "build_compacted_messages_for_api",
    "estimate_messages_tokens",
    "is_overflow",
    "split_head_tail",
    "generate_summary",
]
