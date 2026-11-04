"""Approval policy: trust x capability (spec §4.1).

Decisions: "auto" (run), "approve" (ask the user once), "deny" (refuse).
"""

from __future__ import annotations

from typing import Literal

BLOCKED_CAPABILITIES = frozenset({"network", "side_effect"})

Decision = Literal["auto", "approve", "deny"]


def decide(trust: str, capabilities: list[str], first_run: bool) -> Decision:
    if any(c in BLOCKED_CAPABILITIES for c in capabilities):
        return "deny"  # network/side effects are out of scope for the MVP
    if trust == "candidate":
        return "deny"  # never runs on user data until it passes the gate
    if trust == "verified" and first_run:
        return "approve"
    return "auto"
