"""Checker: top_items. Exact ordered top-N with revenue tolerance."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    top = result.get("top")
    want = expected.get("top") or []
    if not isinstance(top, list) or len(top) != len(want):
        return [f"top has {len(top) if isinstance(top, list) else 'no'} entries, want {len(want)}"]
    for i, (got, exp) in enumerate(zip(top, want, strict=True)):
        if not isinstance(got, dict) or got.get("item") != exp.get("item"):
            fails.append(
                f"top[{i}].item: got {(got or {}).get('item')!r}, want {exp.get('item')!r}"
            )
            continue
        try:
            if abs(float(got.get("revenue")) - float(exp.get("revenue"))) > 0.01:
                fails.append(f"top[{i}].revenue: got {got.get('revenue')!r}")
        except (TypeError, ValueError):
            fails.append(f"top[{i}].revenue: got {got.get('revenue')!r}")
    return fails
