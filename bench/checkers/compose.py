"""Checker: compose. Runs each part family's checker on the merged result."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    from bench.checkers import CHECKERS  # lazy: avoids circular import

    fails: list[str] = []
    for family, part_expected in (expected.get("parts") or {}).items():
        if family not in CHECKERS:
            fails.append(f"unknown part family {family!r}")
            continue
        for f in CHECKERS[family](result, files, part_expected):
            fails.append(f"{family}: {f}")
    return fails
