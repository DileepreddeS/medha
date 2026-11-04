"""Checker: stock_alert. Exact low-stock set."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    got = sorted(result.get("low_stock") or [])
    want = sorted(expected.get("low_stock") or [])
    if got == want:
        return []
    return [f"low_stock: got {got!r}, want {want!r}"]
