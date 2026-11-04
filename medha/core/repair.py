"""Self-repair: failure evidence -> candidate vN+1 -> gate + replay -> swap.

On pass the version switches atomically (old stays for rollback); on fail
vN is kept and the run is marked failed honestly. Never touches holdout
storage directly (the gate owns it).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from medha.core import library, reward
from medha.llm.client import ChatMessage
from medha.llm.prompts import repairer as prompts
from medha.store.db import Database
from medha.store.events import log_event
from medha.store.models import RepairOutput, record_input_roles
from medha.verify import gate as gate_mod
from medha.verify import testgen
from medha.verify.gate import GateReport

BUNDLE_NAME = "__bundle__"


@dataclass
class RepairResult:
    ok: bool
    skill_id: str
    version: int
    gate: GateReport | None
    detail: str


def _encode_inputs(inputs: dict[str, bytes]) -> bytes:
    return json.dumps({k: base64.b64encode(v).decode() for k, v in inputs.items()}).encode()


def _decode_inputs(blob: bytes) -> dict[str, bytes]:
    raw = json.loads(bytes(blob).decode())
    if isinstance(raw, dict) and all(isinstance(v, str) for v in raw.values()):
        try:
            return {k: base64.b64decode(v) for k, v in raw.items()}
        except Exception:
            pass
    return {BUNDLE_NAME: bytes(blob)}


def add_case(
    db: Database, skill_id: str, inputs: dict[str, bytes], expected: dict[str, Any], source: str
) -> int:
    return db.execute(
        "INSERT INTO cases(skill_id, input_name, input_blob, expected_props_json, source)"
        " VALUES (?,?,?,?,?)",
        (skill_id, BUNDLE_NAME, _encode_inputs(inputs), json.dumps(expected), source),
    )


def active_cases(db: Database, skill_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT id, input_name, input_blob, expected_props_json, source FROM cases"
        " WHERE skill_id=? AND superseded_by IS NULL ORDER BY id",
        (skill_id,),
    )
    cases = []
    for r in rows:
        if r["input_name"] == BUNDLE_NAME:
            inputs = _decode_inputs(r["input_blob"])
        else:
            inputs = {r["input_name"]: bytes(r["input_blob"])}
        cases.append(
            {
                "id": r["id"],
                "inputs": inputs,
                "expected": json.loads(r["expected_props_json"]),
                "source": r["source"],
            }
        )
    return cases


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


def repair_skill(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    skill_id: str,
    failure_evidence: str,
    files: dict[str, bytes],
    samples: str,
    new_case: dict[str, Any] | None = None,
    lessons: str = "",
    task_spec: Any = None,
) -> RepairResult:
    current = library.active_version(db, skill_id)
    if current is None:
        return RepairResult(False, skill_id, 0, None, f"unknown skill {skill_id}")
    view = library.read_package(settings.MEDHA_SKILLS_DIR, skill_id, current)
    version = current + 1

    draft = llm.complete_json(
        "repairer",
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(
                role="user",
                content=prompts.user(
                    view.skill_py, view.contract.model_dump_json(), failure_evidence, lessons
                ),
            ),
        ],
        RepairOutput,
    )
    assert isinstance(draft, RepairOutput)
    log_event(
        db,
        type="llm.repairer",
        actor="repairer",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={},
        **_usage_of(llm),
    )

    contract = view.contract.model_copy(update={"version": version, "trust": "candidate"})
    visible = testgen.write_tests(llm, contract, samples, "visible", task_spec)
    log_event(
        db,
        type="llm.testwriter",
        actor="testwriter",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"suite": "visible"},
        **_usage_of(llm),
    )
    holdout = testgen.write_tests(llm, contract, samples, "holdout", task_spec)
    log_event(
        db,
        type="llm.testwriter",
        actor="testwriter",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"suite": "holdout"},
        **_usage_of(llm),
    )
    replay = active_cases(db, skill_id)
    if new_case is not None:
        replay = [*replay, new_case]

    report = gate_mod.run_gate(
        db=db,
        llm=llm,
        runner=runner,
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        skill_py=draft.skill_py,
        skill_md=view.skill_md,
        contract=contract,
        visible_tests=visible,
        holdout_tests=holdout,
        inputs=files,
        replay_cases=replay,
        settings=settings,
        samples=samples,
        task_spec=task_spec,
        # A repair revises vN into vN+1 of the SAME contract: the shape
        # check must use the contract being repaired, not the new task's
        # planner spec (planner key names vary run to run and would fail
        # a correct repair). The judge still verifies task fitness.
        shape_outputs=dict(contract.outputs),
    )
    log_event(
        db,
        type="repair.attempt",
        actor="repairer",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={
            "passed": report.passed,
            "failed_stage": report.failed_stage,
            "notes": draft.notes[:500],
        },
    )
    if not report.passed:
        return RepairResult(False, skill_id, version, report, report.detail)
    library.stage_package(
        settings.MEDHA_SKILLS_DIR,
        skill_id,
        version,
        draft.skill_py,
        view.skill_md,
        contract,
        {
            "source_task_id": task_id,
            "parent_version": current,
            "notes": draft.notes[:500],
            "input_roles": record_input_roles(contract.inputs, files),
        },
    )
    library.write_visible_tests(settings.MEDHA_SKILLS_DIR, skill_id, version, visible)
    library.activate(db, settings.MEDHA_SKILLS_DIR, skill_id, version, "verified", task_id=task_id)
    return RepairResult(True, skill_id, version, report, draft.notes[:500])


def maybe_auto_rollback(db: Database, skill_id: str, version: int, task_id: str = "") -> bool:
    """Restore a good vN when vN+1 falls below E[p]=0.7 in its first 3 runs."""
    if version <= 1 or library.active_version(db, skill_id) != version:
        return False
    if not 2 <= reward.run_count(db, skill_id, version) <= 3:
        return False
    new = db.query_one(
        "SELECT alpha, beta FROM versions WHERE skill_id=? AND version=?", (skill_id, version)
    )
    old = db.query_one(
        "SELECT alpha, beta FROM versions WHERE skill_id=? AND version=?", (skill_id, version - 1)
    )
    if new is None or old is None:
        return False
    ep_new = reward.expected_p(float(new["alpha"]), float(new["beta"]))
    ep_old = reward.expected_p(float(old["alpha"]), float(old["beta"]))
    # Absolute bar (not posterior-vs-posterior: the new version's smaller
    # sample would always lose). Only restores a vN that was actually good.
    if ep_new < 0.7 <= ep_old:
        library.set_active_version(db, skill_id, version - 1, task_id=task_id)
        log_event(
            db,
            type="repair.auto_rollback",
            actor="repairer",
            task_id=task_id,
            skill_id=skill_id,
            version=version - 1,
            payload={"from_version": version, "ep_new": ep_new, "ep_old": ep_old},
        )
        return True
    return False
