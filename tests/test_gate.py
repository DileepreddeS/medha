"""Gate: full pass, holdout-gap rejection, static rejection, replay."""

import json

from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.events import events_for_task
from medha.store.models import SkillContract
from medha.verify import gate as gate_mod
from tests.conftest import StubBackend

SKILL = "def run(inputs, workdir):\n return {'n': 1}\n"
MD = "# S\n\nFine.\n"


def _contract(**kw):
    base = dict(skill_id="s1", version=1, intent="t", postconditions_deterministic=["n == 1"])
    base.update(kw)
    return SkillContract(**base)


def _client(settings):
    judge_json = json.dumps({"verdicts": [], "overall_pass": True})
    meta = StubBackend([(judge_json, 50, 20)])
    return LLMClient(settings, glimmer_backend=StubBackend([]), meta_backend=meta)


def _run(db_settings, runner, contract, **kw):
    settings = db_settings
    db = Database(settings.MEDHA_DB_PATH)
    params = dict(
        db=db,
        llm=_client(settings),
        runner=runner,
        task_id="t1",
        skill_id="s1",
        version=1,
        skill_py=SKILL,
        skill_md=MD,
        contract=contract,
        visible_tests="def test_a(): pass",
        holdout_tests="def test_b(): pass",
        inputs={"in.csv": b"a"},
        replay_cases=[],
        settings=settings,
    )
    params.update(kw)
    return db, gate_mod.run_gate(**params)


def test_gate_full_pass(settings):
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})]
    db, rep = _run(settings, runner, _contract())
    assert rep.passed and rep.result == {"n": 1}
    assert rep.holdout_rate == 1.0
    types = [e["type"] for e in events_for_task(db, "t1")]
    for stage in (
        "gate.static",
        "gate.visible",
        "gate.holdout",
        "gate.outcome",
        "gate.judge",
        "gate.replay",
    ):
        assert stage in types


def test_gate_rejects_holdout_gap(settings):
    runner = FakeSandboxRunner()
    runner.test_outputs = [
        TestRunOutput(passed=["test_a"]),
        TestRunOutput(
            passed=["t1"], failed=[{"name": "t2", "error": "x"}, {"name": "t3", "error": "y"}]
        ),
    ]
    db, rep = _run(settings, runner, _contract())
    assert not rep.passed and rep.failed_stage == "holdout"
    assert rep.holdout_rate < 0.8
    types = [e["type"] for e in events_for_task(db, "t1")]
    assert "gate.outcome" not in types  # stops at first failure


def test_gate_rejects_static(settings):
    runner = FakeSandboxRunner()
    db, rep = _run(settings, runner, _contract(), skill_py="import os\ndef run(i,w):\n return {}")
    assert not rep.passed and rep.failed_stage == "static"
    assert "import" in rep.detail


def test_gate_maps_physical_files_to_logical_inputs(settings):
    """Gate translates fixture filenames to contract logical names.

    Regression: the learn path passed filename-keyed files straight to the
    sandbox while skills (per the author contract) index logical names —
    every visible test died with KeyError on the first live bench run.
    """
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})]
    contract = _contract(inputs={"sales_csv": {"type": "file", "formats": ["csv"]}})
    db, rep = _run(settings, runner, contract, inputs={"sales_v1.csv": b"a,b\n1,2\n"})
    assert rep.passed
    for _kind, _code, call_inputs in runner.calls:
        assert set(call_inputs) == {"sales_csv"}, call_inputs


def test_gate_rejects_unmappable_inputs(settings):
    runner = FakeSandboxRunner()
    contract = _contract(inputs={"sheet": {"type": "file", "formats": ["xlsx"]}})
    db, rep = _run(settings, runner, contract, inputs={"sales_v1.csv": b"a,b\n1,2\n"})
    assert not rep.passed and rep.failed_stage == "inputs"
    assert "xlsx" in rep.detail and "sales_v1.csv" in rep.detail
    assert runner.calls == []  # fails before any sandbox run


def test_gate_judge_sees_input_samples(settings):
    """The judge gets input samples so input-claims are verifiable.

    Regression: semantic postconditions like "sums amount by category
    from sales_csv" were unverifiable from outputs alone, so the judge
    honestly failed them ("no input CSV to verify sums") and killed
    correct skills. Samples are task data, not author reasoning.
    """
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})]
    settings_db = Database(settings.MEDHA_DB_PATH)
    meta = StubBackend([(json.dumps({"verdicts": [], "overall_pass": True}), 50, 20)])
    llm = LLMClient(settings, glimmer_backend=StubBackend([]), meta_backend=meta)
    contract = _contract(postconditions_semantic=["n reflects the input rows"])
    rep = gate_mod.run_gate(
        db=settings_db,
        llm=llm,
        runner=runner,
        task_id="t1",
        skill_id="s1",
        version=1,
        skill_py=SKILL,
        skill_md=MD,
        contract=contract,
        visible_tests="def test_a(): pass",
        holdout_tests="def test_b(): pass",
        inputs={"in.csv": b"a"},
        replay_cases=[],
        settings=settings,
        samples="--- in.csv ---\na,b\n1,2\n",
    )
    assert rep.passed
    user_text = meta.calls[0]["messages"][1]["content"]
    assert "INPUT SAMPLES" in user_text and "in.csv" in user_text


def test_gate_judge_sees_task_goal(settings):
    """The judge gets the task goal so it can catch goal-misses.

    Regression: stock_v2's skill returned a list of dicts where the
    goal prescribed a list of names; every gate layer blessed the
    contract-consistent output because none of them saw the goal.
    """
    from medha.store.models import TaskSpec

    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})]
    settings_db = Database(settings.MEDHA_DB_PATH)
    meta = StubBackend([(json.dumps({"verdicts": [], "overall_pass": True}), 50, 20)])
    llm = LLMClient(settings, glimmer_backend=StubBackend([]), meta_backend=meta)
    contract = _contract(postconditions_semantic=["n reflects the input rows"])
    rep = gate_mod.run_gate(
        db=settings_db,
        llm=llm,
        runner=runner,
        task_id="t1",
        skill_id="s1",
        version=1,
        skill_py=SKILL,
        skill_md=MD,
        contract=contract,
        visible_tests="def test_a(): pass",
        holdout_tests="def test_b(): pass",
        inputs={"in.csv": b"a"},
        replay_cases=[],
        settings=settings,
        task_spec=TaskSpec(goal="Count rows", success_criteria=["n == 1"]),
    )
    assert rep.passed
    user_text = meta.calls[0]["messages"][1]["content"]
    assert "TASK (what the user actually asked)" in user_text
    assert "Count rows" in user_text and "n == 1" in user_text


def test_gate_replay_non_regression(settings):
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["a"]), TestRunOutput(passed=["b"])]
    runner.skill_outputs = [
        SkillRunOutput(ok=True, result={"n": 1}),  # outcome
        SkillRunOutput(ok=True, result={"n": 1}),  # replay ok
        SkillRunOutput(ok=True, result={"n": 2}),  # replay regressed
    ]
    cases = [
        {"inputs": {"a.csv": b"a"}, "expected": {"result_subset": {"n": 1}}},
        {"inputs": {"b.csv": b"b"}, "expected": {"result_subset": {"n": 1}}},
    ]
    db, rep = _run(settings, runner, _contract(), replay_cases=cases)
    assert not rep.passed and rep.failed_stage == "replay"
    assert (rep.replay_passed, rep.replay_total) == (1, 2)


def test_gate_outcome_enforces_spec_shape(settings):
    """Planner-declared outputs are machine-checked at the outcome stage.

    Regression: top_v1's skill returned {"top": "/work/out/top.json"}
    (path string, not the prescribed list) and every gate stage passed
    it. Now the outcome stage fails loud with the key and types.
    """
    from medha.store.models import IOSpec, TaskSpec

    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1, "top": "/work/out/top.json"})]
    spec = TaskSpec(goal="top 2", outputs={"top": IOSpec(type="list")})
    contract = _contract(postconditions_deterministic=["n == 1"])
    _, rep = _run(settings, runner, contract, task_spec=spec)
    assert not rep.passed and rep.failed_stage == "outcome", rep.detail
    assert "top" in rep.detail and "list" in rep.detail


def test_gate_outcome_shape_outputs_override(settings):
    """Explicit shape_outputs replace the task spec (repair passes the
    contract being revised, whose key names the planner may not match)."""
    from medha.store.models import IOSpec, TaskSpec

    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1, "top": "/work/out/top.json"})]
    spec = TaskSpec(goal="top 2", outputs={"top": IOSpec(type="list")})
    contract = _contract(postconditions_deterministic=["n == 1"])
    settings_db = Database(settings.MEDHA_DB_PATH)
    meta = StubBackend([(json.dumps({"verdicts": [], "overall_pass": True}), 50, 20)])
    llm = LLMClient(settings, glimmer_backend=StubBackend([]), meta_backend=meta)
    rep = gate_mod.run_gate(
        db=settings_db,
        llm=llm,
        runner=runner,
        task_id="t1",
        skill_id="s1",
        version=1,
        skill_py=SKILL,
        skill_md=MD,
        contract=contract,
        visible_tests="def test_a(): pass",
        holdout_tests="def test_b(): pass",
        inputs={"in.csv": b"a"},
        replay_cases=[],
        settings=settings,
        task_spec=spec,
        shape_outputs={"n": IOSpec(type="number")},
    )
    assert rep.passed, rep.detail
