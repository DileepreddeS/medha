"""Append-only event log. Every state change is an event; history is immutable."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from medha.store.db import Database


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def log_event(
    db: Database,
    *,
    type: str,
    actor: str,
    task_id: str = "",
    skill_id: str | None = None,
    version: int | None = None,
    payload: dict[str, Any] | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
) -> int:
    return db.execute(
        "INSERT INTO events(ts, task_id, type, actor, skill_id, version, payload_json,"
        " tokens_in, tokens_out, cost_usd, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            utcnow(),
            task_id,
            type,
            actor,
            skill_id,
            version,
            json.dumps(payload or {}),
            tokens_in,
            tokens_out,
            cost_usd,
            latency_ms,
        ),
    )


def events_for_task(db: Database, task_id: str) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,))


def recent_events(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
