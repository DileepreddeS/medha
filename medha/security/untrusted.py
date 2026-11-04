"""Untrusted-data helpers: wrap, truncate, and preview file schemas."""

from __future__ import annotations


def wrap_untrusted(text: str, source: str) -> str:
    return f'<untrusted_data source="{source}">\n{text}\n</untrusted_data>'


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


def schema_preview_csv(data: bytes, max_rows: int = 20, max_line: int = 200) -> str:
    text = data.decode("utf-8", errors="replace")
    lines = [line[:max_line] for line in text.splitlines()[:max_rows]]
    return "\n".join(lines)
