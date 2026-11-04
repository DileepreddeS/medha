"""Repair: v2 via gate+replay; failure keeps v1 honestly."""

import json

from medha.core import library, repair
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.models import SkillContract
from tests.conftest import StubBackend

V1 = "def run(inputs, workdir):\n return {'n': 1}\n"
V2 = "def run(inputs, workdir):\n return {'n': 1}  # fixed\n"


def _setup(settings):
    from medha.store.models import IOSpec

    db = Database(settings.MEDHA_DB_PATH)
    c = SkillContract(
        skill_id="s",
        version=1,
        intent="t",
        outputs={"n": IOSpec(type="number")},
        postconditions_deterministic=["n == 1"],
    )
    library.stage_package(settings.MEDHA_SKILLS_DIR, "s", 1, V1, "# s", c, {})
    library.activate(db, settings.MEDHA_SKILLS_DIR, "s", 1, "verified")
    repair.add_case(db, "s", {"a.csv": b"a"}, {"result_subset": {"n": 1}}, "accepted")
    return db


def _llm(settings):
    glimmer = StubBackend(
        [
            (json.dumps({"skill_py": V2, "notes": "fixed it"}), 50, 50),
            (json.dumps({"tests_py": "def test_v(): pass"}), 10, 10),
            (json.dumps({"tests_py": "def test_h(): pass"}), 10, 10),
        ]
    )
    return LLMClient(settings, glimmer_backend=glimmer, meta_backend=StubBackend([]))


def test_repair_produces_v2(settings):
    db = _setup(settings)
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_v"]), TestRunOutput(passed=["test_h"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})] * 3  # outcome + replay + new
    new_case = {
        "inputs": {"b.csv": b"b"},
        "expected": {"result_subset": {"n": 1}},
        "source": "correction",
    }
    res = repair.repair_skill(
        db=db,
        llm=_llm(settings),
        runner=runner,
        settings=settings,
        task_id="t",
        skill_id="s",
        failure_evidence="boom",
        files={"b.csv": b"b"},
        samples="b",
        new_case=new_case,
    )
    assert res.ok and res.version == 2
    assert library.active_version(db, "s") == 2
    assert res.gate is not None and (res.gate.replay_passed, res.gate.replay_total) == (2, 2)
    assert library.read_package(settings.MEDHA_SKILLS_DIR, "s", 1).skill_py == V1  # kept
    tw = db.query("SELECT * FROM events WHERE task_id='t' AND type='llm.testwriter'")
    assert len(tw) == 2 and all(r["tokens_in"] + r["tokens_out"] > 0 for r in tw)


def test_repair_shape_uses_contract_not_task_spec(settings):
    """A repair revises vN into vN+1 of the same contract, so the shape
    check uses the contract's outputs — a task_spec whose planner named
    keys differently must not fail a correct repair (demo kind-drift)."""
    from medha.store.models import TaskSpec

    db = _setup(settings)
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_v"]), TestRunOutput(passed=["test_h"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})] * 3
    spec = TaskSpec(goal="other names", outputs={})
    res = repair.repair_skill(
        db=db,
        llm=_llm(settings),
        runner=runner,
        settings=settings,
        task_id="t",
        skill_id="s",
        failure_evidence="boom",
        files={"b.csv": b"b"},
        samples="b",
        task_spec=spec,
    )
    assert res.ok and res.version == 2


def test_repair_shape_break_fails_outcome(settings):
    """A revision that breaks the contract shape fails at outcome.
    (True passes "n == 1" but is not a number, isolating the shape row.)"""
    db = _setup(settings)
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"]), TestRunOutput(passed=["t"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": True})]
    res = repair.repair_skill(
        db=db,
        llm=_llm(settings),
        runner=runner,
        settings=settings,
        task_id="t",
        skill_id="s",
        failure_evidence="boom",
        files={"b.csv": b"b"},
        samples="b",
    )
    assert not res.ok and res.gate is not None and res.gate.failed_stage == "outcome"


def test_repair_failure_keeps_v1(settings):
    db = _setup(settings)
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"]), TestRunOutput(passed=["t"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 2})]
    res = repair.repair_skill(
        db=db,
        llm=_llm(settings),
        runner=runner,
        settings=settings,
        task_id="t",
        skill_id="s",
        failure_evidence="boom",
        files={"b.csv": b"b"},
        samples="b",
    )
    assert not res.ok
    assert library.active_version(db, "s") == 1
