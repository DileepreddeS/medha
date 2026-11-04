"""Orchestrator: learn→approve→done, hit with 0 author calls, correction→v2."""

import json

from medha.core import library, orchestrator
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.events import events_for_task
from tests.conftest import StubBackend

SKILL = "def run(inputs, workdir):\n return {'n': 1}\n"
PLAN = json.dumps(
    {
        "goal": "Count rows",
        "inputs": {"data": {"type": "file", "formats": ["csv"]}},
        "outputs": {},
        "success_criteria": ["n == 1"],
        "sub_goals": [],
        "files": ["in.csv"],
    }
)
AUTHOR = json.dumps(
    {
        "skill_py": SKILL,
        "skill_md": "# S\n\nFine.\n",
        "contract": {
            "skill_id": "x",
            "version": 1,
            "intent": "count rows",
            "inputs": {"data": {"type": "file", "formats": ["csv"]}},
            "postconditions_deterministic": ["n == 1"],
        },
    }
)
TESTS = json.dumps({"tests_py": "def test_a():\n assert True\n"})
JUDGE = json.dumps({"verdicts": [], "overall_pass": True})
REPAIR = json.dumps({"skill_py": SKILL + "# v2\n", "notes": "fixed"})


def _rank(skill_id):
    return json.dumps(
        {
            "ranking": [
                {"skill_id": skill_id, "relevance": 0.9, "applicable": "yes", "reason": "match"}
            ]
        }
    )


def test_full_loop_learn_hit_correct(settings):
    db = Database(settings.MEDHA_DB_PATH)
    glimmer = StubBackend(
        [
            (PLAN, 20, 20),
            (AUTHOR, 60, 120),
            (TESTS, 20, 20),
            (TESTS, 20, 20),  # t1 learn
            (PLAN, 20, 20),
            (_rank("count_rows"), 20, 20),  # t2 hit
            (REPAIR, 40, 60),
            (TESTS, 20, 20),
            (TESTS, 20, 20),  # t2 correction
        ]
    )
    meta = StubBackend([(JUDGE, 30, 10)])
    llm = LLMClient(settings, glimmer_backend=glimmer, meta_backend=meta)
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 4
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})] * 7

    # t1: miss → learn → approval gate (first run of a new skill)
    r1 = orchestrator.run_task(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t1",
        task_text="count rows",
        files={"in.csv": b"a,b\n1,2\n"},
    )
    assert r1.status == "needs_approval" and r1.skill_id == "count_rows"
    d1 = orchestrator.decide_approval(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        approval_id=r1.approval_id,
        decision="approve",
    )
    assert d1.status == "done" and d1.result == {"n": 1}

    # t2: variant routes to the existing skill — 0 author calls, $0 LLM cost
    meta_calls_before = len(meta.calls)
    r2 = orchestrator.run_task(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t2",
        task_text="count rows again",
        files={"week2.csv": b"a,b\n3,4\n"},
    )
    assert r2.status == "done" and r2.skill_id == "count_rows"
    types2 = [e["type"] for e in events_for_task(db, "t2")]
    assert "llm.author" not in types2 and "llm.testwriter" not in types2
    assert "gate.judge" not in types2
    assert len(meta.calls) == meta_calls_before  # no Meta spend on reuse

    # correction → v2 that passes replay
    fb = orchestrator.submit_feedback(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t2",
        kind="correct",
        text="exclude refunds",
    )
    assert fb["ok"] and fb["repaired"] and fb["version"] == 2
    assert library.active_version(db, "count_rows") == 2


def test_feedback_accept_reject_and_deny(settings):
    db = Database(settings.MEDHA_DB_PATH)
    glimmer = StubBackend([(PLAN, 5, 5), (AUTHOR, 5, 5), (TESTS, 5, 5), (TESTS, 5, 5)])
    llm = LLMClient(settings, glimmer_backend=glimmer, meta_backend=StubBackend([(JUDGE, 5, 5)]))
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 2
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})] * 2
    r1 = orchestrator.run_task(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t1",
        task_text="count",
        files={"in.csv": b"a"},
    )
    assert (
        orchestrator.submit_feedback(
            db=db, llm=llm, runner=runner, settings=settings, task_id="t1", kind="accept"
        )["ok"]
        is False
    )  # no run yet
    deny = orchestrator.decide_approval(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        approval_id=r1.approval_id,
        decision="deny",
    )
    assert deny.status == "failed"


def test_token_budget_exceeded_fails_gracefully(settings):
    settings.MAX_TOKENS_PER_TASK = 1
    db = Database(settings.MEDHA_DB_PATH)
    llm = LLMClient(settings, glimmer_backend=StubBackend([(PLAN, 50, 50)]))
    r = orchestrator.run_task(
        db=db,
        llm=llm,
        runner=FakeSandboxRunner(),
        settings=settings,
        task_id="t",
        task_text="count",
        files={},
    )
    assert r.status == "failed" and "budget" in r.detail
