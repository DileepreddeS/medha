"""Bench configs A/B/C + repair suite on scripted backends."""

import json
from pathlib import Path

from bench import run as bench_run
from medha.core import library, repair
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from medha.store.models import SkillContract
from tests.conftest import StubBackend

REPO = Path(__file__).resolve().parent.parent
TASKS_YAML = REPO / "bench" / "tasks" / "mini.yaml"
FIXTURES = REPO / "bench" / "fixtures"

SKILL = "def run(inputs, workdir):\n return {'n': 1}\n"
PLAN = json.dumps(
    {
        "goal": "Summarize sales",
        "inputs": {"sales_csv": {"type": "file", "formats": ["csv"]}},
        "outputs": {},
        "success_criteria": [],
        "sub_goals": [],
        "files": [],
    }
)
AUTHOR = json.dumps(
    {
        "skill_py": SKILL,
        "skill_md": "# S\n\nFine.\n",
        "contract": {
            "skill_id": "x",
            "version": 1,
            "intent": "sales",
            "inputs": {"sales_csv": {"type": "file", "formats": ["csv"]}},
            "postconditions_deterministic": ["totals.grand_total == 16.0"],
        },
    }
)
TESTS = json.dumps({"tests_py": "def test_a(): pass\n"})
REPAIR = json.dumps({"skill_py": SKILL, "notes": "fixed"})
SALES_RES = {
    "totals": {"by_category": {"drink": 7.0, "food": 9.0}, "grand_total": 16.0},
    "report_md": "report.md",
}
SALES_FILES = {"report.md": b"# Report\ndrink\nfood\n"}


def _tasks(*ids):
    tasks, _ = bench_run.load_suite(TASKS_YAML)
    return [t for t in tasks if t["id"] in ids]


def test_suite_loads_12_plus_3():
    tasks, repairs = bench_run.load_suite(TASKS_YAML)
    assert len(tasks) == 12 and len(repairs) == 3
    assert len({t["family"] for t in tasks}) == 6
    full = bench_run.load_all(TASKS_YAML)
    assert len(full["heldout"]) == 6 and len(full["compose"]) == 3


def test_config_a_baseline(settings):
    llm = LLMClient(settings, glimmer_backend=StubBackend([(PLAN, 10, 10), (AUTHOR, 20, 30)]))
    runner = FakeSandboxRunner()
    runner.skill_outputs = [
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES))
    ]
    rows = bench_run.run_config_a(_tasks("sales_v1"), FIXTURES, llm, runner)
    assert len(rows) == 1 and rows[0].passed and rows[0].tokens == 70 and not rows[0].reused
    assert rows[0].digest == bench_run.output_digest(SALES_RES, SALES_FILES)


def test_output_digest_is_deterministic():
    d = bench_run.output_digest
    assert d({"b": 1, "a": 2}, {"f": b"x"}) == d({"a": 2, "b": 1}, {"f": b"x"})
    assert d({"a": 1}, {}) != d({"a": 2}, {})
    assert d({}, {"f": b"x"}) != d({}, {"f": b"y"})
    assert len(d({}, {})) == 16


def test_config_b_learn_then_reuse(settings):
    db = Database(settings.MEDHA_DB_PATH)
    rank = json.dumps(
        {
            "ranking": [
                {
                    "skill_id": "summarize_sales",
                    "relevance": 0.9,
                    "applicable": "yes",
                    "reason": "sales",
                }
            ]
        }
    )
    llm = LLMClient(
        settings,
        glimmer_backend=StubBackend([(PLAN, 5, 5), (AUTHOR, 5, 5), (PLAN, 5, 5), (rank, 5, 5)]),
    )
    runner = FakeSandboxRunner()
    runner.skill_outputs = [
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),
        SkillRunOutput(
            ok=True,
            result={"totals": {"by_category": {"drink": 6.0, "food": 2.5}, "grand_total": 8.5}},
            files={"report.md": b"drink\nfood\n"},
        ),
    ]
    rows = bench_run.run_config_b(
        _tasks("sales_v1", "sales_v2"), FIXTURES, db, llm, runner, settings
    )
    assert [r.passed for r in rows] == [True, True]
    assert [r.reused for r in rows] == [False, True]


def test_config_c_full_loop(settings):
    db = Database(settings.MEDHA_DB_PATH)
    llm = LLMClient(
        settings,
        glimmer_backend=StubBackend(
            [(PLAN, 10, 10), (AUTHOR, 10, 10), (TESTS, 10, 10), (TESTS, 10, 10)]
        ),
    )
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 2
    runner.skill_outputs = [
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),
    ]
    rows = bench_run.run_config_c(_tasks("sales_v1"), FIXTURES, db, llm, runner, settings)
    assert len(rows) == 1 and rows[0].passed
    assert rows[0].tokens > 0 and not rows[0].reused


def test_learn_path_records_llm_calls(settings):
    """A learn-path task must leave llm.* events + tokens in the event log.

    Regression guard for the silent 0-token bench failure: the planner's
    empty replies were never recorded, so the summary showed 0 tokens and
    gave no hint any call had been made.
    """
    db = Database(settings.MEDHA_DB_PATH)
    llm = LLMClient(
        settings,
        glimmer_backend=StubBackend(
            [(PLAN, 10, 10), (AUTHOR, 10, 10), (TESTS, 10, 10), (TESTS, 10, 10)]
        ),
    )
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 2
    runner.skill_outputs = [
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),
    ]
    rows = bench_run.run_config_c(_tasks("sales_v1"), FIXTURES, db, llm, runner, settings)
    assert len(rows) == 1 and rows[0].passed
    task_id = "bench-c-0-sales_v1"
    llm_events = db.query("SELECT * FROM events WHERE task_id=? AND type LIKE 'llm.%'", (task_id,))
    assert llm_events, f"learn path recorded zero LLM calls for {task_id}"
    spend = db.query_one(
        "SELECT COALESCE(SUM(tokens_in+tokens_out),0) t FROM events WHERE task_id=?",
        (task_id,),
    )
    assert spend["t"] > 0


def test_repair_suite_on_drift(settings):
    db = Database(settings.MEDHA_DB_PATH)
    c = SkillContract(
        skill_id="sales",
        version=1,
        intent="sales",
        inputs={"sales_csv": {"type": "file", "formats": ["csv"]}},
        postconditions_deterministic=["totals.grand_total == 16.0"],
    )
    library.stage_package(settings.MEDHA_SKILLS_DIR, "sales", 1, SKILL, "# s", c, {})
    library.activate(db, settings.MEDHA_SKILLS_DIR, "sales", 1, "verified")
    repair.add_case(
        db,
        "sales",
        {"sales_csv": b"old"},
        {"result_subset": dict(SALES_RES), "files_present": ["report.md"]},
        "accepted",
    )
    rank = json.dumps(
        {
            "ranking": [
                {"skill_id": "sales", "relevance": 0.9, "applicable": "yes", "reason": "sales"}
            ]
        }
    )
    llm = LLMClient(
        settings,
        glimmer_backend=StubBackend(
            [(PLAN, 5, 5), (rank, 5, 5), (REPAIR, 5, 5), (TESTS, 5, 5), (TESTS, 5, 5)]
        ),
    )
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 2
    runner.skill_outputs = [
        SkillRunOutput(ok=False, error="KeyError: 'amount'"),  # drifted exec fails
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),  # gate outcome
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),  # replay
        SkillRunOutput(ok=True, result=dict(SALES_RES), files=dict(SALES_FILES)),  # re-execute
    ]
    drift = {
        "id": "drift_renamed_column",
        "family": "sales_summary",
        "goal": "Summarize sales",
        "files": {"sales_csv": "sales_r1.csv"},
        "expected": {"by_category": {"drink": 7.0, "food": 9.0}, "grand_total": 16.0},
    }
    rows = bench_run.run_config_c([drift], FIXTURES, db, llm, runner, settings, prefix="bench-r")
    assert len(rows) == 1 and rows[0].passed and rows[0].reused
    assert library.active_version(db, "sales") == 2  # repaired, not held
    failed = db.query(
        "SELECT * FROM events WHERE task_id=? AND type='task.execute_failed'",
        ("bench-r-0-drift_renamed_column",),
    )
    assert len(failed) == 1 and "amount" in failed[0]["payload_json"]
