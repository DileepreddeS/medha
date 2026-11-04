"""Orchestrator: the explicit task state machine (spec §3.2).

RECEIVED → PLANNED → ROUTED → (HIT → EXECUTE → OUTCOME_CHECK | MISS →
LEARN → GATE → REGISTER → EXECUTE) → AWAIT_FEEDBACK → DONE.
Every transition is an event. Repair-on-failure and composition land in P4;
until then failures are reported honestly and multi-goal tasks learn fresh.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medha.core import composer, drift, learner, lessons, library, planner, repair, reward, router
from medha.core.router import RouteDecision
from medha.llm.client import LLMError
from medha.security.approval import decide
from medha.security.untrusted import schema_preview_csv, truncate
from medha.store.db import Database
from medha.store.events import log_event, utcnow
from medha.store.models import TaskSpec
from medha.verify.postconditions import check_all


@dataclass
class TaskResult:
    task_id: str
    status: str  # done | failed | needs_approval
    skill_id: str | None = None
    version: int | None = None
    result: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    detail: str = ""
    approval_id: int | None = None


class TaskBudgetExceeded(LLMError):
    pass


class TaskBudget:
    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self.used = 0
        self._seen: set[int] = set()

    def add(self, resp: Any) -> None:
        if resp is None or id(resp) in self._seen:
            return
        self._seen.add(id(resp))
        self.used += int(resp.tokens_in) + int(resp.tokens_out)
        if self.used > self.max_tokens:
            raise TaskBudgetExceeded(
                f"task token budget exceeded ({self.used} > {self.max_tokens})"
            )


def task_dir(settings: Any, task_id: str) -> Path:
    return Path(settings.MEDHA_DB_PATH).parent / "tasks" / task_id


def save_inputs(settings: Any, task_id: str, files: dict[str, bytes]) -> None:
    dest = task_dir(settings, task_id) / "inputs"
    dest.mkdir(parents=True, exist_ok=True)
    for name, blob in files.items():
        (dest / name).write_bytes(blob)


def load_inputs(settings: Any, task_id: str) -> dict[str, bytes]:
    dest = task_dir(settings, task_id) / "inputs"
    if not dest.exists():
        return {}
    return {p.name: p.read_bytes() for p in sorted(dest.iterdir()) if p.is_file()}


def save_outputs(
    settings: Any, task_id: str, result: dict[str, Any], files: dict[str, bytes]
) -> None:
    dest = task_dir(settings, task_id) / "outputs"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "result.json").write_text(json.dumps(result, default=str, indent=2))
    for name, blob in files.items():
        (dest / name).write_bytes(blob)


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


def _samples_of(files: dict[str, bytes]) -> str:
    parts = []
    for name, blob in sorted(files.items()):
        parts.append(f"--- {name} ---\n{schema_preview_csv(blob)}")
    return truncate("\n\n".join(parts), 6000)


def run_task(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    task_text: str,
    files: dict[str, bytes],
) -> TaskResult:
    try:
        return _run_task(
            db=db,
            llm=llm,
            runner=runner,
            settings=settings,
            task_id=task_id,
            task_text=task_text,
            files=files,
        )
    except (LLMError, TaskBudgetExceeded) as e:
        log_event(
            db,
            type="task.failed",
            actor="orchestrator",
            task_id=task_id,
            payload={"detail": str(e)[:500]},
        )
        return TaskResult(task_id=task_id, status="failed", detail=str(e)[:500])


def _run_task(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    task_text: str,
    files: dict[str, bytes],
) -> TaskResult:
    log_event(
        db,
        type="task.received",
        actor="orchestrator",
        task_id=task_id,
        payload={"files": sorted(files)},
    )
    save_inputs(settings, task_id, files)
    budget = TaskBudget(settings.MAX_TOKENS_PER_TASK)

    schemas = {name: schema_preview_csv(blob) for name, blob in files.items()}
    spec = planner.plan(llm, task_text, schemas)
    budget.add(llm.last)
    log_event(
        db,
        type="task.planned",
        actor="planner",
        task_id=task_id,
        payload={"goal": spec.goal},
        **_usage_of(llm),
    )

    decision = router.route(
        db=db,
        llm=llm,
        skills_dir=settings.MEDHA_SKILLS_DIR,
        task_id=task_id,
        task_spec=spec,
        files=files,
        route_min=settings.ROUTE_MIN,
    )
    budget.add(llm.last)
    log_event(
        db,
        type="task.routed",
        actor="router",
        task_id=task_id,
        skill_id=decision.skill_id,
        version=decision.version,
        payload={
            "outcome": decision.outcome,
            "reason": decision.reason,
            "relevance": decision.relevance,
        },
    )

    if decision.outcome == "hit":
        assert decision.skill_id and decision.version
        return _execute(
            db=db,
            llm=llm,
            runner=runner,
            settings=settings,
            task_id=task_id,
            spec=spec,
            skill_id=decision.skill_id,
            version=decision.version,
            mapping=decision.mapping,
            files=files,
        )
    if decision.outcome == "compose":
        composed = composer.compose_skill(
            db=db,
            llm=llm,
            runner=runner,
            settings=settings,
            task_id=task_id,
            task_spec=spec,
            child_ids=decision.compose_ids,
            files=files,
            samples=_samples_of(files),
        )
        budget.add(llm.last)
        if composed.ok:
            return _request_first_approval(
                db,
                task_id,
                composed.skill_id,
                composed.version,
                f"Composed {composed.skill_id} v{composed.version} from "
                f"{', '.join(composed.child_ids)}; approval required.",
            )
        log_event(
            db,
            type="compose.failed",
            actor="orchestrator",
            task_id=task_id,
            payload={"detail": composed.detail[:300]},
        )
    if decision.outcome == "miss" and decision.probe_failed and decision.skill_id:
        repaired = _try_probe_repair(
            db=db,
            llm=llm,
            runner=runner,
            settings=settings,
            task_id=task_id,
            spec=spec,
            decision=decision,
            files=files,
            budget=budget,
        )
        if repaired is not None:
            return repaired

    learned = learner.learn_skill(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id=task_id,
        task_spec=spec,
        files=files,
        samples=_samples_of(files),
        lessons=lessons.lessons_for_prompt(db, ""),
    )
    if not learned.ok:
        try:
            lessons.create_lesson(
                db,
                scope="global",
                trigger=f"learn failed for: {spec.goal[:120]}",
                guidance="Author output rejected by the gate; simplify the contract.",
                evidence_ids=[],
                task_id=task_id,
            )
        except lessons.LessonRejected:
            pass
        once = learner.solve_once(
            db=db,
            llm=llm,
            runner=runner,
            task_id=task_id,
            task_spec=spec,
            files=files,
            samples=_samples_of(files),
        )
        if once is not None:
            save_outputs(settings, task_id, once.result, once.files)
            log_event(
                db,
                type="task.done",
                actor="orchestrator",
                task_id=task_id,
                payload={"unverified": True},
            )
            return TaskResult(
                task_id=task_id,
                status="done",
                result=once.result,
                files=once.files,
                detail="Solved once without saving a skill (unverified).",
            )
        log_event(
            db,
            type="task.failed",
            actor="orchestrator",
            task_id=task_id,
            skill_id=learned.skill_id,
            payload={"detail": (learned.gate.detail if learned.gate else "learn failed")},
        )
        return TaskResult(
            task_id=task_id,
            status="failed",
            skill_id=learned.skill_id,
            detail="could not learn a verified skill; nothing was saved",
        )

    return _request_first_approval(
        db,
        task_id,
        learned.skill_id,
        learned.version,
        f"Learned {learned.skill_id} v{learned.version}; approval required.",
    )


def _request_first_approval(
    db: Database, task_id: str, skill_id: str, version: int, detail: str
) -> TaskResult:
    approval_id = db.execute(
        "INSERT INTO approvals(task_id, skill_id, version, decision, ts) VALUES (?,?,?,?,?)",
        (task_id, skill_id, version, "pending", utcnow()),
    )
    log_event(
        db,
        type="approval.requested",
        actor="orchestrator",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"approval_id": approval_id},
    )
    return TaskResult(
        task_id=task_id,
        status="needs_approval",
        skill_id=skill_id,
        version=version,
        approval_id=approval_id,
        detail=detail,
    )


def _try_probe_repair(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    spec: TaskSpec,
    decision: RouteDecision,
    files: dict[str, bytes],
    budget: TaskBudget,
) -> TaskResult | None:
    assert decision.skill_id
    past = [c for c in repair.active_cases(db, decision.skill_id) if c["source"] == "accepted"]
    diff = drift.describe_diff(past[-1]["inputs"], files) if past else "no accepted cases yet"
    evidence = (
        f"Precondition probe failed for {decision.skill_id}: {decision.reason}. "
        f"Task files: {sorted(files)}. Task goal: {spec.goal[:200]}\n"
        f"Schema diff vs last accepted inputs:\n{diff}"
    )
    rep = repair.repair_skill(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id=task_id,
        skill_id=decision.skill_id,
        failure_evidence=evidence,
        files=files,
        samples=_samples_of(files),
        task_spec=spec,
    )
    budget.add(llm.last)
    if not rep.ok:
        log_event(
            db,
            type="repair.declined",
            actor="orchestrator",
            task_id=task_id,
            skill_id=decision.skill_id,
            payload={"detail": rep.detail[:300]},
        )
        return None
    view = library.read_package(settings.MEDHA_SKILLS_DIR, rep.skill_id, rep.version)
    mapping = router.map_inputs(view.contract, files)
    if mapping is None:
        return None
    return _execute(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id=task_id,
        spec=spec,
        skill_id=rep.skill_id,
        version=rep.version,
        mapping=mapping,
        files=files,
        pre_approved=True,
    )


def _execute(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    spec: TaskSpec,
    skill_id: str,
    version: int,
    mapping: dict[str, str],
    files: dict[str, bytes],
    pre_approved: bool = False,
    _retried: bool = False,
) -> TaskResult:
    view = library.read_package(settings.MEDHA_SKILLS_DIR, skill_id, version)
    row = db.query_one("SELECT trust FROM skills WHERE id=?", (skill_id,))
    trust = row["trust"] if row else "candidate"
    first_run = reward.run_count(db, skill_id, version) == 0
    if not pre_approved:
        verdict = decide(trust, view.contract.capabilities, first_run)
        if verdict == "deny":
            return TaskResult(
                task_id=task_id,
                status="failed",
                skill_id=skill_id,
                version=version,
                detail="blocked by approval policy",
            )
        if verdict == "approve":
            approval_id = db.execute(
                "INSERT INTO approvals(task_id, skill_id, version, decision, ts)"
                " VALUES (?,?,?,?,?)",
                (task_id, skill_id, version, "pending", utcnow()),
            )
            log_event(
                db,
                type="approval.requested",
                actor="orchestrator",
                task_id=task_id,
                skill_id=skill_id,
                version=version,
                payload={"approval_id": approval_id},
            )
            return TaskResult(
                task_id=task_id,
                status="needs_approval",
                skill_id=skill_id,
                version=version,
                approval_id=approval_id,
                detail="First run of this skill needs approval.",
            )
    mapped = {logical: files[fname] for logical, fname in mapping.items()}
    out = runner.run_skill(view.skill_py, mapped)
    import time

    start_ms = int(time.monotonic() * 1000)
    checks = check_all(
        out.result if out.ok else {}, out.files, view.contract.postconditions_deterministic
    )
    latency_ms = int(time.monotonic() * 1000) - start_ms
    if out.ok and all(c["passed"] for c in checks):
        reward.update(
            db,
            skill_id=skill_id,
            version=version,
            reward=1.0,
            latency_ms=latency_ms,
            task_id=task_id,
        )
        repair.maybe_auto_rollback(db, skill_id, version, task_id=task_id)
        repair.add_case(
            db,
            skill_id,
            mapped,
            {"result_subset": out.result, "files_present": sorted(out.files)},
            "accepted",
        )
        save_outputs(settings, task_id, out.result, out.files)
        log_event(
            db,
            type="task.done",
            actor="orchestrator",
            task_id=task_id,
            skill_id=skill_id,
            version=version,
            payload={"checks": len(checks)},
        )
        return TaskResult(
            task_id=task_id,
            status="done",
            skill_id=skill_id,
            version=version,
            result=out.result,
            files=out.files,
        )
    reward.update(db, skill_id=skill_id, version=version, reward=0.0, task_id=task_id)
    repair.maybe_auto_rollback(db, skill_id, version, task_id=task_id)
    detail = (
        out.error[:300]
        if not out.ok
        else "; ".join(c["postcondition"] for c in checks if not c["passed"])[:300]
    )
    log_event(
        db,
        type="task.execute_failed",
        actor="orchestrator",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"detail": detail, "run_ok": out.ok},
    )
    if not _retried:
        rep = repair.repair_skill(
            db=db,
            llm=llm,
            runner=runner,
            settings=settings,
            task_id=task_id,
            skill_id=skill_id,
            failure_evidence=f"Execution failed: {detail}",
            files=mapped,
            samples=_samples_of(mapped),
        )
        if rep.ok:
            view2 = library.read_package(settings.MEDHA_SKILLS_DIR, rep.skill_id, rep.version)
            mapping2 = router.map_inputs(view2.contract, files)
            if mapping2 is not None:
                return _execute(
                    db=db,
                    llm=llm,
                    runner=runner,
                    settings=settings,
                    task_id=task_id,
                    spec=spec,
                    skill_id=rep.skill_id,
                    version=rep.version,
                    mapping=mapping2,
                    files=files,
                    pre_approved=True,
                    _retried=True,
                )
    if reward.should_repair(db, skill_id, version)[0]:
        try:
            lessons.create_lesson(
                db,
                scope=skill_id,
                trigger=f"{skill_id} v{version} execution failed",
                guidance="Execution failed outcome checks; verify input schema match.",
                evidence_ids=[],
                task_id=task_id,
            )
        except lessons.LessonRejected:
            pass
    log_event(
        db,
        type="task.failed",
        actor="orchestrator",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"detail": detail},
    )
    return TaskResult(
        task_id=task_id, status="failed", skill_id=skill_id, version=version, detail=detail
    )


def decide_approval(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    approval_id: int,
    decision: str,
) -> TaskResult:
    row = db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
    if row is None:
        return TaskResult(task_id="", status="failed", detail="unknown approval")
    if row["decision"] != "pending":
        return TaskResult(
            task_id=row["task_id"], status="failed", detail=f"already decided: {row['decision']}"
        )
    if decision not in ("approve", "deny"):
        return TaskResult(task_id=row["task_id"], status="failed", detail="bad decision")
    db.execute("UPDATE approvals SET decision=? WHERE id=?", (decision, approval_id))
    log_event(
        db,
        type="approval.decided",
        actor="user",
        task_id=row["task_id"],
        skill_id=row["skill_id"],
        version=row["version"],
        payload={"approval_id": approval_id, "decision": decision},
    )
    if decision == "deny":
        return TaskResult(
            task_id=row["task_id"],
            status="failed",
            skill_id=row["skill_id"],
            version=row["version"],
            detail="denied by user",
        )
    files = load_inputs(settings, row["task_id"])
    view = library.read_package(settings.MEDHA_SKILLS_DIR, row["skill_id"], row["version"])
    mapping = router.map_inputs(view.contract, files)
    if mapping is None:
        return TaskResult(
            task_id=row["task_id"],
            status="failed",
            skill_id=row["skill_id"],
            version=row["version"],
            detail="inputs no longer match the contract",
        )
    return _execute(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id=row["task_id"],
        spec=TaskSpec(goal="(approved continuation)"),
        skill_id=row["skill_id"],
        version=row["version"],
        mapping=mapping,
        files=files,
        pre_approved=True,
    )


def submit_feedback(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    kind: str,
    text: str = "",
) -> dict[str, Any]:
    run = db.query_one("SELECT * FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
    if run is None:
        return {"ok": False, "detail": "no run recorded for task"}
    if kind == "accept":
        log_event(
            db,
            type="feedback.accepted",
            actor="user",
            task_id=task_id,
            skill_id=run["skill_id"],
            version=run["version"],
            payload={},
        )
        return {"ok": True}
    if kind == "reject":
        reward.adjust(db, run_id=run["id"], new_reward=0.0, task_id=task_id)
        log_event(
            db,
            type="feedback.rejected",
            actor="user",
            task_id=task_id,
            skill_id=run["skill_id"],
            version=run["version"],
            payload={},
        )
        return {"ok": True}
    if kind == "correct":
        reward.adjust(db, run_id=run["id"], new_reward=0.5, task_id=task_id)
        try:
            lessons.create_lesson(
                db,
                scope=run["skill_id"],
                trigger="user correction",
                guidance=text or "User corrected the result.",
                evidence_ids=[],
                task_id=task_id,
            )
        except lessons.LessonRejected as e:
            return {"ok": False, "detail": f"correction rejected: {e}"}
        files = load_inputs(settings, task_id)
        view = library.read_package(settings.MEDHA_SKILLS_DIR, run["skill_id"], run["version"])
        mapping = router.map_inputs(view.contract, files)
        if mapping is None:
            return {"ok": False, "detail": "inputs no longer match the skill contract"}
        logical = {k: files[v] for k, v in mapping.items()}
        case_id = repair.add_case(db, run["skill_id"], logical, {"notes": text[:500]}, "correction")
        rep = repair.repair_skill(
            db=db,
            llm=llm,
            runner=runner,
            settings=settings,
            task_id=task_id,
            skill_id=run["skill_id"],
            failure_evidence=f"User correction: {text[:1000]}",
            files=files,
            samples=_samples_of(files),
            new_case={
                "id": case_id,
                "inputs": logical,
                "expected": {"notes": text[:500]},
                "source": "correction",
            },
        )
        log_event(
            db,
            type="feedback.corrected",
            actor="user",
            task_id=task_id,
            skill_id=run["skill_id"],
            version=rep.version,
            payload={"repaired": rep.ok, "detail": rep.detail[:300]},
        )
        return {"ok": True, "repaired": rep.ok, "version": rep.version, "detail": rep.detail}
    return {"ok": False, "detail": f"unknown feedback kind {kind!r}"}
