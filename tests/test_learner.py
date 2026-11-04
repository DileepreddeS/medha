"""Learner: learn → gate → register; retry then honest failure."""

import json

from medha.core import learner as learner_mod
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.events import events_for_task
from medha.store.models import TaskSpec
from tests.conftest import StubBackend

SKILL = "def run(inputs, workdir):\n return {'n': 1}\n"

AUTHOR_JSON = json.dumps(
    {
        "skill_py": SKILL,
        "skill_md": "# S\n\nFine.\n",
        "contract": {
            "skill_id": "ignored",
            "version": 99,
            "intent": "count",
            "postconditions_deterministic": ["n == 1"],
        },
    }
)
TESTS_JSON = json.dumps({"tests_py": "def test_a():\n assert True\n"})
JUDGE_JSON = json.dumps({"verdicts": [], "overall_pass": True})


def _client(settings, author_attempts=1):
    glimmer = StubBackend(
        [(AUTHOR_JSON, 100, 200), (TESTS_JSON, 50, 50), (TESTS_JSON, 50, 50)] * author_attempts
    )
    meta = StubBackend([(JUDGE_JSON, 50, 20)] * author_attempts)
    return LLMClient(settings, glimmer_backend=glimmer, meta_backend=meta)


def _runner(passes=True):
    r = FakeSandboxRunner()
    r.test_outputs = [TestRunOutput(passed=["test_a"]), TestRunOutput(passed=["test_b"])]
    r.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1 if passes else 2})]
    return r


def test_learn_registers_verified_skill(settings):
    db = Database(settings.MEDHA_DB_PATH)
    res = learner_mod.learn_skill(
        db=db,
        llm=_client(settings),
        runner=_runner(),
        settings=settings,
        task_id="t1",
        task_spec=TaskSpec(goal="Count rows"),
        files={"in.csv": b"a"},
        samples="a,b\n1,2",
    )
    assert res.ok and res.attempts == 1 and not res.escalated
    assert res.skill_id == "count_rows"  # deterministic slug, not LLM id
    assert res.version == 1  # enforced, not the LLM's version=99
    row = db.query_one("SELECT trust, active_version FROM skills WHERE id=?", ("count_rows",))
    assert (row["trust"], row["active_version"]) == ("verified", 1)
    types = [e["type"] for e in events_for_task(db, "t1")]
    assert "skill.registered" in types and "learn.attempt" in types


def test_learn_records_input_roles(settings):
    from medha.core import library

    author = json.dumps(
        {
            "skill_py": SKILL,
            "skill_md": "# S\n\nFine.\n",
            "contract": {
                "skill_id": "ignored",
                "version": 1,
                "intent": "count",
                "inputs": {"data": {"type": "file", "formats": ["csv"]}},
                "postconditions_deterministic": ["n == 1"],
            },
        }
    )
    glimmer = StubBackend([(author, 1, 1), (TESTS_JSON, 1, 1), (TESTS_JSON, 1, 1)])
    meta = StubBackend([(JUDGE_JSON, 1, 1)])
    db = Database(settings.MEDHA_DB_PATH)
    res = learner_mod.learn_skill(
        db=db,
        llm=LLMClient(settings, glimmer_backend=glimmer, meta_backend=meta),
        runner=_runner(),
        settings=settings,
        task_id="t1",
        task_spec=TaskSpec(goal="Count rows"),
        files={"in.csv": b"category,amount\nfood,1\n"},
        samples="category,amount\nfood,1",
    )
    assert res.ok
    view = library.read_package(settings.MEDHA_SKILLS_DIR, res.skill_id, 1)
    assert view.provenance["input_roles"] == {"data": {"amount": "amount", "category": "category"}}


def test_learn_fails_honestly_after_3_attempts(settings):
    db = Database(settings.MEDHA_DB_PATH)
    res = learner_mod.learn_skill(
        db=db,
        llm=_client(settings, author_attempts=3),
        runner=_runner(passes=False),
        settings=settings,
        task_id="t1",
        task_spec=TaskSpec(goal="Count rows"),
        files={"in.csv": b"a"},
        samples="a",
    )
    assert not res.ok and res.attempts == 3
    assert res.gate is not None and res.gate.failed_stage == "outcome"
    assert db.query("SELECT * FROM skills") == []  # nothing registered


def test_escalation_uses_meta_provider(settings):
    settings.ALLOW_ESCALATION = True
    # attempts 1-2: glimmer authors, outcome fails. attempt 3: meta authors, passes.
    glimmer = StubBackend(
        [(AUTHOR_JSON, 1, 1), (TESTS_JSON, 1, 1), (TESTS_JSON, 1, 1)] * 2
        + [(TESTS_JSON, 1, 1), (TESTS_JSON, 1, 1)]  # attempt-3 testwriter
    )
    meta = StubBackend([(AUTHOR_JSON, 1, 1), (JUDGE_JSON, 1, 1)])  # attempt-3 author + judge
    llm = LLMClient(settings, glimmer_backend=glimmer, meta_backend=meta)
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 6
    runner.skill_outputs = [
        SkillRunOutput(ok=True, result={"n": 2}),
        SkillRunOutput(ok=True, result={"n": 2}),
        SkillRunOutput(ok=True, result={"n": 1}),
    ]
    db = Database(settings.MEDHA_DB_PATH)
    res = learner_mod.learn_skill(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t1",
        task_spec=TaskSpec(goal="Count rows"),
        files={"in.csv": b"a"},
        samples="a",
    )
    assert res.ok and res.escalated and res.attempts == 3
    assert meta.calls[0]["model"] == "muse-spark-1.3"
    assert glimmer.calls and all(c["model"] == "glimmer" for c in glimmer.calls)
