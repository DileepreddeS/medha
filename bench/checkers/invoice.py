"""Checker: invoice_total. Grand total within a cent."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    try:
        if abs(float(result.get("total")) - float(expected["total"])) <= 0.01:
            return []
    except (TypeError, ValueError, KeyError):
        pass
    return [f"total: got {result.get('total')!r}, want {expected.get('total')}"]
