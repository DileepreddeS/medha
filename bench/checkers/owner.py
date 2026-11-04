"""Checker: owner_summary. Report states range, total, and top category."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    blob = "\n".join(v.decode("utf-8", errors="replace") for v in files.values())
    fails = []
    for needle in expected.get("contains") or []:
        if str(needle) not in blob:
            fails.append(f"report missing {needle!r}")
    return fails
