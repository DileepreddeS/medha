"""Composer: thin composite over child skills + lineage; unknown child fails."""

import json

from medha.core import composer, library
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.models import SkillContract, SubGoal, TaskSpec
from medha.verify.static import static_check
from tests.conftest import StubBackend

CHILD = "def run(inputs, workdir):\n return {'n': 1}\n"
COMPOSITE = "import child_0\nimport child_1\ndef run(inputs, workdir):\n return {'n': 1}\n"


def _setup(settings):
    db = Database(settings.MEDHA_DB_PATH)
    for cid in ("c1", "c2"):
        c = SkillContract(
            skill_id=cid, version=1, intent=f"child {cid}", postconditions_deterministic=["n == 1"]
        )
        library.stage_package(settings.MEDHA_SKILLS_DIR, cid, 1, CHILD, f"# {cid}", c, {})
        library.activate(db, settings.MEDHA_SKILLS_DIR, cid, 1, "verified")
    return db


def test_compose_ok_with_lineage(settings):
    db = _setup(settings)
    comp_json = json.dumps(
        {
            "skill_py": COMPOSITE,
            "contract": {
                "skill_id": "x",
                "version": 1,
                "intent": "both",
                "postconditions_deterministic": ["n == 1"],
            },
        }
    )
    llm = LLMClient(
        settings,
        glimmer_backend=StubBackend(
            [
                (comp_json, 40, 60),
                (json.dumps({"tests_py": "def test_v(): pass"}), 5, 5),
                (json.dumps({"tests_py": "def test_h(): pass"}), 5, 5),
            ]
        ),
    )
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"]), TestRunOutput(passed=["t"])]
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})]
    spec = TaskSpec(goal="Do both", sub_goals=[SubGoal(name="a"), SubGoal(name="b")])
    res = composer.compose_skill(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t",
        task_spec=spec,
        child_ids=["c1", "c2"],
        files={"in.csv": b"a"},
        samples="a",
    )
    assert res.ok and res.version == 1
    view = library.read_package(settings.MEDHA_SKILLS_DIR, res.skill_id, 1)
    assert view.provenance["composed_of"] == ["c1", "c2"]
    assert "import child_0" in view.skill_py
    tw = db.query("SELECT * FROM events WHERE task_id='t' AND type='llm.testwriter'")
    assert len(tw) == 2 and all(r["tokens_in"] + r["tokens_out"] > 0 for r in tw)


def test_composite_imports_need_extra_allowlist():
    c = SkillContract(skill_id="x", version=1, intent="t", postconditions_deterministic=["n == 1"])
    assert static_check(COMPOSITE, "# x", c).passed is False
    assert static_check(COMPOSITE, "# x", c, frozenset({"child_0", "child_1"})).passed is True


def test_compose_unknown_child(settings):
    db = _setup(settings)
    llm = LLMClient(settings, glimmer_backend=StubBackend([]))
    res = composer.compose_skill(
        db=db,
        llm=llm,
        runner=FakeSandboxRunner(),
        settings=settings,
        task_id="t",
        task_spec=TaskSpec(goal="x"),
        child_ids=["nope"],
        files={},
        samples="",
    )
    assert not res.ok and "unknown child" in res.detail
