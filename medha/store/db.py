"""SQLite (WAL) storage. One connection per call; safe under threads."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL, actor TEXT NOT NULL,
  skill_id TEXT, version INTEGER, payload_json TEXT NOT NULL DEFAULT '{}',
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS skills(
  id TEXT PRIMARY KEY, active_version INTEGER NOT NULL DEFAULT 1,
  trust TEXT NOT NULL DEFAULT 'candidate', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
  skill_id TEXT NOT NULL, version INTEGER NOT NULL,
  path TEXT NOT NULL, alpha REAL NOT NULL DEFAULT 0,
  beta REAL NOT NULL DEFAULT 0, cost_ema REAL NOT NULL DEFAULT 0,
  latency_ema REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'candidate',
  PRIMARY KEY(skill_id, version));
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, skill_id TEXT NOT NULL,
  version INTEGER NOT NULL, outcome TEXT NOT NULL, reward REAL NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0,
  ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY, skill_id TEXT NOT NULL, input_name TEXT NOT NULL,
  input_blob BLOB NOT NULL, expected_props_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL, superseded_by INTEGER);
CREATE TABLE IF NOT EXISTS lessons(
  id INTEGER PRIMARY KEY, scope TEXT NOT NULL, trigger_text TEXT NOT NULL,
  guidance TEXT NOT NULL, evidence_event_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, skill_id TEXT NOT NULL,
  version INTEGER NOT NULL, decision TEXT NOT NULL DEFAULT 'pending', ts TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._conn() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
