"""Drift helpers: describe input-schema changes for repair evidence."""

from __future__ import annotations


def _csv_headers(blob: bytes) -> list[str]:
    try:
        first = blob.decode("utf-8", errors="replace").splitlines()[0]
    except IndexError:
        return []
    return [c.strip().strip('"') for c in first.split(",") if c.strip()]


def describe_diff(old: dict[str, bytes], new: dict[str, bytes]) -> str:
    """Human-readable schema diff between past-good inputs and current ones."""
    lines: list[str] = []
    for name in sorted(set(new) - set(old)):
        lines.append(f"new file: {name} ({len(new[name])} bytes)")
    for name in sorted(set(old) - set(new)):
        lines.append(f"missing file: {name}")
    for name in sorted(set(old) & set(new)):
        if name.lower().endswith((".csv", ".txt")):
            oh, nh = _csv_headers(old[name]), _csv_headers(new[name])
            for c in nh:
                if c not in oh:
                    lines.append(f"{name}: new column {c!r}")
            for c in oh:
                if c not in nh:
                    lines.append(f"{name}: dropped column {c!r}")
        if len(old[name]) != len(new[name]):
            lines.append(f"{name}: size {len(old[name])} -> {len(new[name])} bytes")
    return "\n".join(lines) if lines else "no schema differences detected"


def resembles(old: dict[str, bytes], new: dict[str, bytes]) -> bool:
    """Do the new inputs look like a drifted variant of past-good ones?"""
    if not old or not new:
        return False
    old_exts = {n.rsplit(".", 1)[-1].lower() for n in old if "." in n}
    new_exts = {n.rsplit(".", 1)[-1].lower() for n in new if "." in n}
    if old_exts and new_exts and not (old_exts & new_exts):
        return False
    for name in set(old) & set(new):
        if name.lower().endswith(".csv"):
            oh, nh = set(_csv_headers(old[name])), set(_csv_headers(new[name]))
            if oh and nh and len(oh & nh) / max(len(oh | nh), 1) < 0.4:
                return False
    return True
