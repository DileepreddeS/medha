"""Checker: receipt_categorization. Exact per-row category sequence."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    got = result.get("categories")
    want = expected.get("categories") or []
    if list(got or []) == list(want):
        return []
    return [f"categories: got {got!r}, want {want!r}"]
