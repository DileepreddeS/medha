"""Checker: sales_summary. Totals reconcile + report names every category."""

from __future__ import annotations

from typing import Any


def check(result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    totals = result.get("totals")
    if not isinstance(totals, dict):
        return ["missing totals object"]
    by_cat = totals.get("by_category")
    if not isinstance(by_cat, dict):
        fails.append("missing totals.by_category")
        by_cat = {}
    for cat, want in (expected.get("by_category") or {}).items():
        got = by_cat.get(cat)
        try:
            ok = got is not None and abs(float(got) - float(want)) <= 0.01
        except (TypeError, ValueError):
            ok = False
        if not ok:
            fails.append(f"by_category[{cat!r}]: got {got!r}, want {want}")
    try:
        grand_ok = abs(float(totals.get("grand_total")) - float(expected["grand_total"])) <= 0.01
    except (TypeError, ValueError, KeyError):
        grand_ok = False
    if not grand_ok:
        fails.append(f"grand_total: got {totals.get('grand_total')!r}")
    texts = [v.decode("utf-8", errors="replace") for v in files.values()]
    blob = "\n".join(texts)
    for cat in expected.get("by_category") or {}:
        if str(cat) not in blob:
            fails.append(f"report does not mention {cat!r}")
    return fails
