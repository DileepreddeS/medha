"""API request/response models (Pydantic v2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class TaskResponse(BaseModel):
    task_id: str
    status: str
    skill_id: str | None = None
    version: int | None = None
    result: dict[str, Any] = {}
    file_names: list[str] = []
    detail: str = ""
    approval_id: int | None = None


class FeedbackRequest(BaseModel):
    kind: Literal["accept", "correct", "reject"]
    text: str = ""


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "deny"]


class RollbackRequest(BaseModel):
    to_version: int


class SkillSummary(BaseModel):
    id: str
    active_version: int
    trust: str
    expected_p: float
    runs: int
    cost_per_run: float
    versions: list[int]


class SkillDetail(BaseModel):
    id: str
    contract: dict[str, Any]
    skill_md: str
    skill_py: str = ""
    prev_skill_py: str | None = None
    prev_version: int | None = None
    versions: list[dict[str, Any]]
    lineage: list[str] = []
    recent_runs: list[dict[str, Any]] = []


class StatsResponse(BaseModel):
    spend_today_usd: float
    spend_cap_usd: float
    tokens_total: int
    tasks_total: int
    reuse_rate: float
    skills: int
    versions: int


class EventOut(BaseModel):
    id: int
    ts: str
    type: str
    actor: str
    skill_id: str | None = None
    version: int | None = None
    payload: dict[str, Any] = {}
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
