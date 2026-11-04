"""Verification gate (spec §3.5): static → visible → holdout → outcome →
judge → replay. Stops at the first failure; every stage emits an event.

This is the ONLY module allowed to touch holdout tests and the judge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from medha.store.db import Database
from medha.store.events import log_event
from medha.store.models import JudgeOutput, SkillContract, map_inputs
from medha.verify.holdout import store as holdout_store
from medha.verify.judge import judge
from medha.verify.postconditions import check_all, check_spec_shape, match_expected, texts_of
from medha.verify.static import StaticReport, static_check


@dataclass
class GateReport:
    passed: bool
    failed_stage: str = ""
    detail: str = ""
    visible_passed: int = 0
    visible_total: int = 0
    holdout_passed: int = 0
    holdout_total: int = 0
    holdout_rate: float = 0.0
    outcome_checks: list[dict[str, Any]] = field(default_factory=list)
    judge: JudgeOutput | None = None
    replay_passed: int = 0
    replay_total: int = 0
    result: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)


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


def run_gate(
    *,
    db: Database,
    llm: Any,
    runner: Any,
    task_id: str,
    skill_id: str,
    version: int,
    skill_py: str,
    skill_md: str,
    contract: SkillContract,
    visible_tests: str,
    holdout_tests: str,
    inputs: dict[str, bytes],
    replay_cases: list[dict[str, Any]],
    settings: Any,
    extra_modules: dict[str, str] | None = None,
    samples: str = "",
    task_spec: Any = None,
    shape_outputs: dict[str, Any] | None = None,
) -> GateReport:
    report = GateReport(passed=False)
    common: dict[str, Any] = dict(task_id=task_id, skill_id=skill_id, version=version)

    # 1. static
    static: StaticReport = static_check(
        skill_py, skill_md, contract, frozenset((extra_modules or {}).keys())
    )
    log_event(
        db,
        type="gate.static",
        actor="gate",
        payload={"passed": static.passed, "findings": [f.__dict__ for f in static.findings][:20]},
        **common,
    )
    if not static.passed:
        report.failed_stage = "static"
        report.detail = "; ".join(f"{f.rule}: {f.message}" for f in static.findings[:5])
        return report

    # 1b. translate physical files to logical contract inputs so the skill
    # and both test suites receive the logical keys the prompts promise.
    mapping = map_inputs(contract.inputs, inputs)
    if mapping is None:
        want = {
            k: (v.formats or ([v.format] if v.format else [])) for k, v in contract.inputs.items()
        }
        report.failed_stage = "inputs"
        report.detail = f"contract inputs {want} do not match files {sorted(inputs)}"
        log_event(
            db,
            type="gate.inputs",
            actor="gate",
            payload={"passed": False, "detail": report.detail},
            **common,
        )
        return report
    inputs = {logical: inputs[fname] for logical, fname in mapping.items()}
    log_event(
        db,
        type="gate.inputs",
        actor="gate",
        payload={"passed": True, "mapping": mapping},
        **common,
    )

    # persist hidden tests (verifier scratch until activation)
    holdout_store.write_holdout(
        settings.MEDHA_VERIFY_DIR, skill_id, version, holdout_tests, inputs, overwrite=True
    )

    # 2. visible
    vis = runner.run_tests(skill_py, visible_tests, inputs, extra_modules)
    report.visible_passed, report.visible_total = len(vis.passed), vis.total
    log_event(
        db,
        type="gate.visible",
        actor="gate",
        payload={"passed": vis.passed, "failed": vis.failed, "error": vis.error[:2000]},
        **common,
    )
    if not vis.ok or vis.total == 0:
        report.failed_stage = "visible"
        report.detail = (
            vis.error or "; ".join(f["name"] for f in vis.failed) or "no tests collected"
        )
        return report

    # 3. holdout (read back from the hidden store, never from the author)
    ho_tests, ho_inputs = holdout_store.read_holdout(settings.MEDHA_VERIFY_DIR, skill_id, version)
    ho = runner.run_tests(skill_py, ho_tests, ho_inputs, extra_modules)
    report.holdout_passed, report.holdout_total = len(ho.passed), ho.total
    report.holdout_rate = len(ho.passed) / ho.total if ho.total else 0.0
    overfit = report.holdout_rate < settings.HOLDOUT_MIN
    log_event(
        db,
        type="gate.holdout",
        actor="gate",
        payload={
            "passed": ho.passed,
            "failed": ho.failed,
            "rate": report.holdout_rate,
            "min": settings.HOLDOUT_MIN,
            "overfit": overfit,
            "error": ho.error[:2000],
        },
        **common,
    )
    if ho.error or ho.total == 0 or overfit:
        report.failed_stage = "holdout"
        report.detail = ho.error or (
            f"holdout rate {report.holdout_rate:.2f} < {settings.HOLDOUT_MIN}"
        )
        return report

    # 4. outcome on the real inputs
    out = runner.run_skill(skill_py, inputs, extra_modules)
    checks = check_all(
        out.result if out.ok else {}, out.files, contract.postconditions_deterministic
    )
    if shape_outputs is None:
        shape_outputs = task_spec.outputs if task_spec is not None else {}
    checks = checks + check_spec_shape(out.result if out.ok else {}, shape_outputs or {})
    report.outcome_checks = checks
    log_event(
        db,
        type="gate.outcome",
        actor="gate",
        payload={"ok": out.ok, "checks": checks, "error": out.error[:2000]},
        **common,
    )
    if not out.ok or not all(c["passed"] for c in checks):
        report.failed_stage = "outcome"
        bad = [c for c in checks if not c["passed"]]
        report.detail = (
            out.error[:500] if not out.ok else "; ".join(c["postcondition"] for c in bad[:3])
        )
        return report
    report.result, report.files = out.result, out.files

    # 5. judge (semantic)
    verdict = judge(llm, contract, out.result, texts_of(out.files), samples, task_spec)
    report.judge = verdict
    log_event(
        db,
        type="gate.judge",
        actor="judge",
        payload={
            "overall_pass": verdict.overall_pass,
            "verdicts": [v.model_dump() for v in verdict.verdicts],
        },
        **_usage_of(llm),
        **common,
    )
    if not verdict.overall_pass:
        report.failed_stage = "judge"
        report.detail = "semantic postconditions failed"
        return report

    # 6. replay non-regression (revisions only)
    passed = 0
    replay_failures: list[dict[str, Any]] = []
    for i, case in enumerate(replay_cases):
        rerun = runner.run_skill(skill_py, case["inputs"], extra_modules)
        fails = [] if not rerun.ok else match_expected(rerun.result, rerun.files, case["expected"])
        if rerun.ok and not fails:
            passed += 1
        else:
            replay_failures.append({"case": i, "error": rerun.error[:500], "mismatches": fails[:5]})
    report.replay_passed, report.replay_total = passed, len(replay_cases)
    log_event(
        db,
        type="gate.replay",
        actor="gate",
        payload={"passed": passed, "total": len(replay_cases), "failures": replay_failures[:5]},
        **common,
    )
    if replay_failures:
        report.failed_stage = "replay"
        report.detail = f"{len(replay_failures)} replay case(s) regressed"
        return report

    report.passed = True
    return report
