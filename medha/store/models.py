"""Pydantic v2 models for every boundary: LLM I/O, skill contracts, DB rows."""

from __future__ import annotations

import csv
import io
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IOSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    formats: list[str] = Field(default_factory=list)
    format: str | None = None
    optional: bool = False
    schema_ref: str | None = None


class SkillContract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_id: str
    version: int = 1
    intent: str
    inputs: dict[str, IOSpec] = Field(default_factory=dict)
    outputs: dict[str, IOSpec] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    postconditions_deterministic: list[str] = Field(default_factory=list)
    postconditions_semantic: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=lambda: ["read_input", "write_output"])
    applicability_notes: str = ""
    trust: str = "candidate"


class SubGoal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    needs: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=list)


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal: str
    inputs: dict[str, IOSpec] = Field(default_factory=dict)
    outputs: dict[str, IOSpec] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    sub_goals: list[SubGoal] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


class AuthorOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_py: str
    skill_md: str
    contract: SkillContract


class TestSuiteOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tests_py: str


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    postcondition: str
    passed: bool = Field(alias="pass")
    quote: str = ""


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdicts: list[Verdict] = Field(default_factory=list)
    overall_pass: bool


class RepairOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_py: str
    notes: str = ""


class CompositeOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_py: str
    contract: SkillContract


class RankItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_id: str
    relevance: float = 0.0
    applicable: str = "unsure"
    reason: str = ""


class RoutingOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ranking: list[RankItem] = Field(default_factory=list)


class LessonRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scope: str
    trigger: str
    guidance: str
    evidence_event_ids: list[int] = Field(default_factory=list)


class CaseProps(BaseModel):
    """Expected properties of a replay case (loose: checkers vary)."""

    model_config = ConfigDict(extra="allow")

    notes: str = ""


def map_inputs(
    contract_inputs: dict[str, IOSpec], files: dict[str, bytes]
) -> dict[str, str] | None:
    """Map contract logical input names to physical files. None = no match.

    Pure shared helper: the gate, router, learner, and bench all translate
    through here so skills always receive logical-keyed ``inputs``. A file
    already keyed by its logical name passes through untouched (exact
    match wins); otherwise required inputs match by extension, optional
    inputs are skipped when nothing matches, and empty files never match.
    """
    remaining = dict(files)
    mapping: dict[str, str] = {}
    ordered = sorted(contract_inputs, key=lambda n: bool(contract_inputs[n].optional))
    for logical in ordered:
        spec = contract_inputs[logical]
        if logical in remaining:
            if len(remaining[logical]) == 0:
                return None
            mapping[logical] = logical
            del remaining[logical]
            continue
        fmts = [f.lower().lstrip(".") for f in (spec.formats or [])]
        if spec.format:
            fmts.append(spec.format.lower().lstrip("."))
        pick = None
        for fname in sorted(remaining):
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if not fmts or ext in fmts:
                pick = fname
                break
        if pick is None:
            if spec.optional:
                continue
            return None
        if len(remaining[pick]) == 0:
            return None
        mapping[logical] = pick
        del remaining[pick]
    return mapping


ROLE_ALIASES: dict[str, list[str]] = {
    "amount": ["amount", "total", "gross", "net", "revenue", "price", "cost", "sale", "sales"],
    "category": ["category", "cat", "type", "class", "group"],
    "date": ["date", "day"],
    "item": ["item", "product", "name"],
}
"""Column roles for the schema probe. The author prompt carries the same
table in prose (kept in sync by test_prompts); skills resolve columns by
role so reuse survives header renames."""


def resolve_roles(headers: list[str]) -> dict[str, str]:
    """Map each resolvable role to its header. Exact match wins over
    substring, first header wins within a pass."""
    norm = [(h.strip().lower(), h) for h in headers]
    roles: dict[str, str] = {}
    for role, aliases in ROLE_ALIASES.items():
        exact = next((h for n, h in norm if n in aliases), None)
        if exact is not None:
            roles[role] = exact
            continue
        sub = next((h for n, h in norm if any(a in n for a in aliases)), None)
        if sub is not None:
            roles[role] = sub
    return roles


def csv_headers(blob: bytes) -> list[str] | None:
    """First-row headers of a CSV blob; None when not parseable tabular."""
    try:
        text = blob.decode("utf-8-sig")
    except (UnicodeDecodeError, ValueError):
        return None
    try:
        rows = list(csv.reader(io.StringIO(text.splitlines()[0] if text else "")))
    except (csv.Error, IndexError):
        return None
    if not rows or not rows[0]:
        return None
    return [h for h in rows[0] if h.strip()]


def record_input_roles(
    contract_inputs: dict[str, IOSpec], files: dict[str, bytes]
) -> dict[str, dict[str, str]]:
    """Resolve column roles per logical input for the registration record.

    The router's schema probe re-resolves these roles on every HIT and
    routes to repair when a recorded role no longer resolves — never a
    silent wrong answer on drifted schemas.
    """
    mapping = map_inputs(contract_inputs, files)
    if mapping is None:
        return {}
    recorded: dict[str, dict[str, str]] = {}
    for logical, fname in mapping.items():
        headers = csv_headers(files[fname])
        if headers:
            roles = resolve_roles(headers)
            if roles:
                recorded[logical] = roles
    return recorded


class EventRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: str = ""
    task_id: str = ""
    type: str
    actor: str
    skill_id: str | None = None
    version: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
