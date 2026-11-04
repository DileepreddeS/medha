"""Orchestrator compose path: compose decision → composite → approval."""

import json

from medha.core import library, orchestrator
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.models import SkillContract
from tests.conftest import StubBackend

CHILD = "def run(inputs, workdir):\n return {'n': 1}\n"
PLAN = json.dumps(
    {
        "goal": "Report plus invoice",
        "inputs": {"data": {"type": "file", "formats": ["csv"]}},
        "outputs": {},
        "success_criteria": ["n == 1"],
        "sub_goals": [{"name": "a"}, {"name": "b"}],
        "files": ["in.csv"],
    }
)
RANK = json.dumps(
    {
        "ranking": [
            {"skill_id": "c1", "relevance": 0.85, "applicable": "yes", "reason": "a"},
            {"skill_id": "c2", "relevance": 0.8, "applicable": "yes", "reason": "b"},
        ]
    }
)
COMP = json.dumps(
    {
        "skill_py": "import child_0\ndef run(inputs, workdir):\n return {'n': 1}\n",
        "contract": {
            "skill_id": "x",
            "version": 1,
            "intent": "both",
            "inputs": {"data": {"type": "file", "formats": ["csv"]}},
            "postconditions_deterministic": ["n == 1"],
        },
    }
)
TESTS = json.dumps({"tests_py": "def test_a(): pass\n"})


def test_compose_path(settings):
    db = Database(settings.MEDHA_DB_PATH)
    for cid in ("c1", "c2"):
        c = SkillContract(
            skill_id=cid, version=1, intent=f"child {cid}", postconditions_deterministic=["n == 1"]
        )
        library.stage_package(settings.MEDHA_SKILLS_DIR, cid, 1, CHILD, f"# {cid}", c, {})
        library.activate(db, settings.MEDHA_SKILLS_DIR, cid, 1, "verified")
    llm = LLMClient(
        settings,
        glimmer_backend=StubBackend(
            [(PLAN, 5, 5), (RANK, 5, 5), (COMP, 5, 5), (TESTS, 5, 5), (TESTS, 5, 5)]
        ),
    )
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 2
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})]
    res = orchestrator.run_task(
        db=db,
        llm=llm,
        runner=runner,
        settings=settings,
        task_id="t",
        task_text="report plus invoice",
        files={"in.csv": b"a,b\n1,2\n"},
    )
    assert res.status == "needs_approval"
    assert res.skill_id is not None and res.skill_id.startswith("composed_")
    view = library.read_package(settings.MEDHA_SKILLS_DIR, res.skill_id, 1)
    assert view.provenance["composed_of"] == ["c1", "c2"]
