"""Composer: chain existing skills into one thin composite skill.

The composite calls child run() functions in DAG order (never inlines
them), gets its own contract + gate run, and records lineage in
provenance.composed_of for the UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from medha.core import learner, library
from medha.llm.client import ChatMessage
from medha.llm.prompts import composer as prompts
from medha.store.db import Database
from medha.store.events import log_event
from medha.store.models import CompositeOutput, TaskSpec, record_input_roles
from medha.verify import gate as gate_mod
from medha.verify import testgen
from medha.verify.gate import GateReport


@dataclass
class ComposeResult:
    ok: bool
    skill_id: str
    version: int
    gate: GateReport | None
    child_ids: list[str]
    detail: str = ""


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


def compose_skill(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    settings: Any,
    task_id: str,
    task_spec: TaskSpec,
    child_ids: list[str],
    files: dict[str, bytes],
    samples: str,
) -> ComposeResult:
    children = []
    for cid in child_ids:
        ver = library.active_version(db, cid)
        if ver is None:
            return ComposeResult(False, "", 0, None, child_ids, f"unknown child {cid}")
        children.append((cid, ver, library.read_package(settings.MEDHA_SKILLS_DIR, cid, ver)))
    children_json = json.dumps(
        [
            {
                "module": f"child_{i}",
                "skill_id": cid,
                "version": ver,
                "contract": json.loads(view.contract.model_dump_json()),
                "input_keys": sorted(view.contract.inputs),
                "output_keys": sorted(view.contract.outputs),
            }
            for i, (cid, ver, view) in enumerate(children)
        ]
    )
    draft = llm.complete_json(
        "composer",
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(
                role="user",
                content=prompts.user(task_spec.model_dump_json(), children_json),
            ),
        ],
        CompositeOutput,
    )
    assert isinstance(draft, CompositeOutput)
    skill_id = learner.unique_skill_id(db, learner.slugify("composed " + task_spec.goal))
    version = 1
    log_event(
        db,
        type="llm.composer",
        actor="composer",
        task_id=task_id,
        skill_id=skill_id,
        payload={"children": child_ids},
        **_usage_of(llm),
    )
    draft.contract.skill_id = skill_id
    draft.contract.version = version
    extra_modules = {f"child_{i}": view.skill_py for i, (_, _, view) in enumerate(children)}

    visible = testgen.write_tests(llm, draft.contract, samples, "visible", task_spec)
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
    holdout = testgen.write_tests(llm, draft.contract, samples, "holdout", task_spec)
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
    report = gate_mod.run_gate(
        db=db,
        llm=llm,
        runner=runner,
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        skill_py=draft.skill_py,
        skill_md=f"# {skill_id}\n\nComposite of: {', '.join(child_ids)}.\n",
        contract=draft.contract,
        visible_tests=visible,
        holdout_tests=holdout,
        inputs=files,
        replay_cases=[],
        settings=settings,
        extra_modules=extra_modules,
        samples=samples,
        task_spec=task_spec,
    )
    log_event(
        db,
        type="compose.attempt",
        actor="composer",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"passed": report.passed, "failed_stage": report.failed_stage},
    )
    if not report.passed:
        return ComposeResult(False, skill_id, version, report, child_ids, report.detail)
    library.stage_package(
        settings.MEDHA_SKILLS_DIR,
        skill_id,
        version,
        draft.skill_py,
        f"# {skill_id}\n\nComposite of: {', '.join(child_ids)}.\n",
        draft.contract,
        {
            "source_task_id": task_id,
            "composed_of": child_ids,
            "input_roles": record_input_roles(draft.contract.inputs, files),
        },
    )
    library.write_visible_tests(settings.MEDHA_SKILLS_DIR, skill_id, version, visible)
    library.activate(db, settings.MEDHA_SKILLS_DIR, skill_id, version, "verified", task_id=task_id)
    return ComposeResult(True, skill_id, version, report, child_ids)
