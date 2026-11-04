"""Learner: author ⇄ isolated test writer → gate → register.

At most 3 authoring attempts; after 2 failures, escalate to Muse Spark 1.3
when ALLOW_ESCALATION=1. Never touches holdout storage (firewall).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from medha.core import author as author_mod
from medha.core import library
from medha.sandbox.runner import SkillRunOutput
from medha.store.db import Database
from medha.store.events import log_event
from medha.store.models import TaskSpec, map_inputs, record_input_roles
from medha.verify import gate as gate_mod
from medha.verify import testgen
from medha.verify.gate import GateReport
from medha.verify.static import static_check

MAX_ATTEMPTS = 3


@dataclass
class LearnResult:
    ok: bool
    skill_id: str
    version: int
    gate: GateReport | None
    attempts: int
    escalated: bool


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "skill"


def unique_skill_id(db: Database, base: str) -> str:
    candidate, i = base, 2
    while db.query_one("SELECT id FROM skills WHERE id=?", (candidate,)):
        candidate = f"{base}_{i}"
        i += 1
    return candidate


def _usage_of(llm: Any) -> dict[str, Any]:
    last = getattr(llm, "last", None)
    if last is None:
        return {}
    return {
        "tokens_in": last.tokens_in,
        "tokens_out": last.tokens_out,
        "cost_usd": last.cost_usd,
        "latency_ms": last.latency_ms,
    }


def learn_skill(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    task_spec: TaskSpec,
    files: dict[str, bytes],
    samples: str,
    lessons: str = "",
) -> LearnResult:
    skill_id = unique_skill_id(db, slugify(task_spec.goal))
    version = 1
    escalated = False
    last_gate: GateReport | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        role = "author"
        if attempt == MAX_ATTEMPTS and settings.ALLOW_ESCALATION:
            role = "escalation"
            escalated = True
            log_event(
                db,
                type="task.escalated",
                actor="learner",
                task_id=task_id,
                skill_id=skill_id,
                payload={"attempt": attempt},
            )
        draft = author_mod.draft_skill(llm, role, task_spec, samples, lessons)
        log_event(
            db,
            type="llm.author",
            actor=role,
            task_id=task_id,
            skill_id=skill_id,
            payload={"attempt": attempt},
            **_usage_of(llm),
        )
        draft.contract.skill_id = skill_id  # deterministic IDs, not LLM-chosen
        draft.contract.version = version

        visible = testgen.write_tests(llm, draft.contract, samples, "visible", task_spec)
        log_event(
            db,
            type="llm.testwriter",
            actor="testwriter",
            task_id=task_id,
            skill_id=skill_id,
            payload={"suite": "visible"},
            **_usage_of(llm),
        )
        holdout = testgen.write_tests(llm, draft.contract, samples, "holdout", task_spec)
        log_event(
            db,
            type="llm.testwriter",
            actor="testwriter",
            task_id=task_id,
            skill_id=skill_id,
            payload={"suite": "holdout"},
            **_usage_of(llm),
        )

        last_gate = gate_mod.run_gate(
            db=db,
            llm=llm,
            runner=runner,
            task_id=task_id,
            skill_id=skill_id,
            version=version,
            skill_py=draft.skill_py,
            skill_md=draft.skill_md,
            contract=draft.contract,
            visible_tests=visible,
            holdout_tests=holdout,
            inputs=files,
            replay_cases=[],
            settings=settings,
            samples=samples,
            task_spec=task_spec,
        )
        log_event(
            db,
            type="learn.attempt",
            actor="learner",
            task_id=task_id,
            skill_id=skill_id,
            version=version,
            payload={
                "attempt": attempt,
                "passed": last_gate.passed,
                "failed_stage": last_gate.failed_stage,
                "detail": last_gate.detail[:500],
            },
        )
        if last_gate.passed:
            library.stage_package(
                settings.MEDHA_SKILLS_DIR,
                skill_id,
                version,
                draft.skill_py,
                draft.skill_md,
                draft.contract,
                {
                    "source_task_id": task_id,
                    "attempt": attempt,
                    "escalated": escalated,
                    "input_roles": record_input_roles(draft.contract.inputs, files),
                },
            )
            library.write_visible_tests(settings.MEDHA_SKILLS_DIR, skill_id, version, visible)
            library.activate(
                db, settings.MEDHA_SKILLS_DIR, skill_id, version, "verified", task_id=task_id
            )
            return LearnResult(True, skill_id, version, last_gate, attempt, escalated)

    return LearnResult(False, skill_id, version, last_gate, MAX_ATTEMPTS, escalated)


def solve_once(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    task_id: str,
    task_spec: TaskSpec,
    files: dict[str, bytes],
    samples: str,
) -> SkillRunOutput | None:
    """Fallback after 3 failed attempts: solve once without saving a skill.

    The code is still statically scanned and sandboxed; it is just never
    registered. Returns None when even this fails (honest failure).
    """
    draft = author_mod.draft_skill(llm, "author", task_spec, samples)
    log_event(
        db,
        type="llm.author",
        actor="author",
        task_id=task_id,
        payload={"attempt": "solve-once"},
        **_usage_of(llm),
    )
    findings = static_check(draft.skill_py, "# once", draft.contract).findings
    if findings:
        log_event(
            db,
            type="task.solve_once",
            actor="learner",
            task_id=task_id,
            payload={"ok": False, "detail": "static scan failed"},
        )
        return None
    mapping = map_inputs(draft.contract.inputs, files)
    if mapping is None:
        log_event(
            db,
            type="task.solve_once",
            actor="learner",
            task_id=task_id,
            payload={"ok": False, "detail": "inputs do not match the drafted contract"},
        )
        return None
    out = runner.run_skill(draft.skill_py, {k: files[v] for k, v in mapping.items()})
    log_event(
        db,
        type="task.solve_once",
        actor="learner",
        task_id=task_id,
        payload={"ok": out.ok, "detail": out.error[:300]},
    )
    return out if out.ok else None
