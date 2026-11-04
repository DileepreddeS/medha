"""Router: miss/hit/probe/compose + input mapping + Beta scoring."""

import json

from medha.core import library, reward, router
from medha.core.router import map_inputs
from medha.llm.client import LLMClient
from medha.store.db import Database
from medha.store.models import SkillContract, SubGoal, TaskSpec
from tests.conftest import StubBackend

SKILL = "def run(inputs, workdir):\n return {'n': 1}\n"


def _register(settings, db, skill_id, intent, inputs, preconditions=("has data",), provenance=None):
    c = SkillContract(
        skill_id=skill_id,
        version=1,
        intent=intent,
        inputs=inputs,
        preconditions=list(preconditions),
        postconditions_deterministic=["n == 1"],
    )
    library.stage_package(
        settings.MEDHA_SKILLS_DIR, skill_id, 1, SKILL, f"# {skill_id}", c, provenance or {}
    )
    library.activate(db, settings.MEDHA_SKILLS_DIR, skill_id, 1, "verified")
    return c


def _setup(settings):
    db = Database(settings.MEDHA_DB_PATH)
    _register(
        settings,
        db,
        "sales",
        "summarize weekly POS sales csv",
        {"sales_csv": {"type": "file", "formats": ["csv"]}},
    )
    _register(
        settings,
        db,
        "invoice",
        "extract totals from supplier invoice pdf",
        {"invoice_pdf": {"type": "file", "formats": ["pdf"]}},
    )
    return db


def _route(db, settings, spec, files, ranking):
    llm = LLMClient(settings, glimmer_backend=StubBackend([(ranking, 10, 10)]))
    return llm, router.route(
        db=db,
        llm=llm,
        skills_dir=settings.MEDHA_SKILLS_DIR,
        task_id="t",
        task_spec=spec,
        files=files,
        route_min=0.6,
    )


def test_miss_on_empty_library(settings):
    db = Database(settings.MEDHA_DB_PATH)
    llm = LLMClient(settings, glimmer_backend=StubBackend([]))
    d = router.route(
        db=db,
        llm=llm,
        skills_dir=settings.MEDHA_SKILLS_DIR,
        task_id="t",
        task_spec=TaskSpec(goal="x"),
        files={},
        route_min=0.6,
    )
    assert d.outcome == "miss" and d.reason == "empty library"


def test_hit_with_mapping(settings):
    db = _setup(settings)
    spec = TaskSpec(goal="summarize weekly sales csv", success_criteria=["totals reconcile"])
    ranking = json.dumps(
        {
            "ranking": [
                {"skill_id": "sales", "relevance": 0.9, "applicable": "yes", "reason": "csv sales"},
                {"skill_id": "invoice", "relevance": 0.1, "applicable": "no", "reason": "pdf only"},
            ]
        }
    )
    _, d = _route(db, settings, spec, {"pos.csv": b"date,amount\n2026-01-01,5\n"}, ranking)
    assert d.outcome == "hit" and d.skill_id == "sales" and d.version == 1
    assert d.mapping == {"sales_csv": "pos.csv"}
    assert d.expected_p == 0.5  # fresh Beta(0,0)


def test_miss_below_route_min(settings):
    db = _setup(settings)
    ranking = json.dumps(
        {
            "ranking": [
                {"skill_id": "sales", "relevance": 0.2, "applicable": "unsure", "reason": "?"},
                {"skill_id": "invoice", "relevance": 0.1, "applicable": "unsure", "reason": "?"},
            ]
        }
    )
    _, d = _route(db, settings, TaskSpec(goal="translate french poetry"), {"a.txt": b"x"}, ranking)
    assert d.outcome == "miss" and "relevance" in d.reason


def test_probe_failure_routes_to_repair_not_silent_answer(settings):
    db = _setup(settings)
    ranking = json.dumps(
        {
            "ranking": [
                {"skill_id": "invoice", "relevance": 0.95, "applicable": "yes", "reason": "totals"},
                {"skill_id": "sales", "relevance": 0.1, "applicable": "no", "reason": "not sales"},
            ]
        }
    )
    _, d = _route(db, settings, TaskSpec(goal="sum a csv"), {"data.csv": b"a\n1"}, ranking)
    assert d.outcome == "miss" and d.probe_failed and d.skill_id == "invoice"


def test_schema_probe_blocks_role_drift_but_allows_renames(settings):
    """Recorded column roles must re-resolve on HIT inputs.

    Regression: a drifted header ("kind") silently miscategorized rows
    while deterministic outcome checks passed on consistent-but-wrong
    output. Renames that preserve roles ("cat"/"total_usd") still reuse.
    """
    db = Database(settings.MEDHA_DB_PATH)
    _register(
        settings,
        db,
        "sales",
        "summarize weekly POS sales csv",
        {"sales_csv": {"type": "file", "formats": ["csv"]}},
        provenance={"input_roles": {"sales_csv": {"category": "category", "amount": "amount"}}},
    )
    spec = TaskSpec(goal="summarize weekly sales csv")

    def rank():
        return json.dumps(
            {
                "ranking": [
                    {
                        "skill_id": "sales",
                        "relevance": 0.9,
                        "applicable": "yes",
                        "reason": "sales",
                    }
                ]
            }
        )

    _, hit = _route(db, settings, spec, {"w2.csv": b"day,product,cat,total_usd\n1,2,3,4\n"}, rank())
    assert hit.outcome == "hit", hit.reason
    _, miss = _route(db, settings, spec, {"w3.csv": b"date,item,kind,total\n1,2,3,4\n"}, rank())
    assert miss.outcome == "miss" and miss.probe_failed and miss.skill_id == "sales"
    assert "category" in miss.reason


def test_contradicted_yes_downgrades_to_miss(settings):
    """A yes-vote whose reason names a gap is dropped (miss, learn fresh).

    Regression: three mini-C forced HITs carried reasons naming the gap
    ("hardcodes Whole Foods/Shell instead of required Trader Joes/BP",
    "does not mention date range") yet voted yes and failed the tasks.
    """
    from medha.core.router import contradicts_yes

    assert contradicts_yes(
        "Matches per-row categorization but hardcodes Whole Foods/Shell"
        " instead of required Trader Joes/BP."
    )
    assert contradicts_yes("Writes markdown report but does not mention date range.")
    assert contradicts_yes(
        "Produces category revenue markdown but never mentions date range or top category."
    )
    # owner_v1 forced HIT: "...but contract never guarantees date range
    # or exact date format." Bare "never" (not just "never mention"):
    # across 11 econ-C HIT reasons, "never" appeared only in this fail.
    assert contradicts_yes("Provides totals but contract never guarantees date range.")
    # Compose-demo forced HIT: "Covers sales-by-category and markdown
    # but omits reorder list needed for full ops pack." "omit" covers
    # omits/omitted/omission; no confident HIT reason uses the word.
    assert contradicts_yes("Covers sales but omits reorder list needed for ops pack.")
    assert not contradicts_yes("Covers sales totals, grand total, and markdown report.")
    assert not contradicts_yes("Contract matches CSV input to Markdown report.")
    assert not contradicts_yes("csv sales")

    db = _setup(settings)
    ranking = json.dumps(
        {
            "ranking": [
                {
                    "skill_id": "sales",
                    "relevance": 0.9,
                    "applicable": "yes",
                    "reason": "Matches sales but does not cover dates.",
                },
                {"skill_id": "invoice", "relevance": 0.1, "applicable": "no", "reason": "pdf"},
            ]
        }
    )
    _, d = _route(db, settings, TaskSpec(goal="owner summary with dates"), {"a.csv": b"x"}, ranking)
    assert d.outcome == "miss", d.reason


def test_unsure_gap_reason_downgrades_to_miss(settings):
    """An unsure-vote whose reason names a gap is dropped, not HIT.

    Regression: receipts_v2's rerank voted applicable="unsure" (rel
    0.75) with reason "...specifies Whole Foods/Shell rules instead of
    required Trader Joes/BP rules." The guard only vetoed "yes" votes,
    so the unsure vote scored, the task forced-HIT, and it failed.
    """
    db = _setup(settings)
    ranking = json.dumps(
        {
            "ranking": [
                {
                    "skill_id": "sales",
                    "relevance": 0.75,
                    "applicable": "unsure",
                    "reason": "Matches structure but specifies X instead of required Y.",
                },
                {"skill_id": "invoice", "relevance": 0.1, "applicable": "no", "reason": "pdf"},
            ]
        }
    )
    _, d = _route(
        db, settings, TaskSpec(goal="summarize weekly sales csv"), {"a.csv": b"x"}, ranking
    )
    assert d.outcome == "miss", d.reason


def test_beta_score_prefers_reliable_skill(settings):
    db = _setup(settings)
    for _ in range(5):
        reward.update(db, skill_id="sales", version=1, reward=1.0)
    reward.update(db, skill_id="invoice", version=1, reward=0.0)
    ranking = json.dumps(
        {
            "ranking": [
                {"skill_id": "sales", "relevance": 0.7, "applicable": "yes", "reason": "ok"},
                {"skill_id": "invoice", "relevance": 0.7, "applicable": "yes", "reason": "ok"},
            ]
        }
    )
    _, d = _route(
        db, settings, TaskSpec(goal="data totals"), {"a.csv": b"x", "b.pdf": b"%PDF"}, ranking
    )
    assert d.outcome == "hit" and d.skill_id == "sales"


def test_compose_when_subgoals_covered(settings):
    db = _setup(settings)
    spec = TaskSpec(goal="report plus invoice", sub_goals=[SubGoal(name="s"), SubGoal(name="i")])
    ranking = json.dumps(
        {
            "ranking": [
                {"skill_id": "sales", "relevance": 0.8, "applicable": "yes", "reason": "s"},
                {"skill_id": "invoice", "relevance": 0.8, "applicable": "yes", "reason": "i"},
            ]
        }
    )
    _, d = _route(db, settings, spec, {"a.csv": b"x", "b.pdf": b"%PDF"}, ranking)
    assert d.outcome == "compose" and set(d.compose_ids) == {"sales", "invoice"}


def test_compose_from_honest_partials(settings):
    """Unsure + gap-marker candidates can compose on multi-goal tasks, never HIT."""
    db = _setup(settings)
    spec = TaskSpec(goal="report plus invoice", sub_goals=[SubGoal(name="s"), SubGoal(name="i")])
    ranking = json.dumps(
        {
            "ranking": [
                {
                    "skill_id": "sales",
                    "relevance": 0.5,
                    "applicable": "unsure",
                    "reason": "omit due dates",
                },
                {
                    "skill_id": "invoice",
                    "relevance": 0.5,
                    "applicable": "unsure",
                    "reason": "never saw report",
                },
            ]
        }
    )
    _, d = _route(db, settings, spec, {"a.csv": b"x", "b.pdf": b"%PDF"}, ranking)
    assert d.outcome == "compose" and set(d.compose_ids) == {"sales", "invoice"}


def test_single_unsure_partial_is_miss(settings):
    db = _setup(settings)
    ranking = json.dumps(
        {
            "ranking": [
                {
                    "skill_id": "sales",
                    "relevance": 0.7,
                    "applicable": "unsure",
                    "reason": "omit due dates",
                },
            ]
        }
    )
    _, d = _route(db, settings, TaskSpec(goal="tally"), {"a.csv": b"x"}, ranking)
    assert d.outcome == "miss", d.reason


def test_map_inputs_unit():
    c = SkillContract(
        skill_id="s",
        version=1,
        intent="t",
        inputs={
            "req": {"type": "file", "formats": ["csv"]},
            "opt": {"type": "file", "formats": ["pdf"], "optional": True},
        },
    )
    assert map_inputs(c, {"a.csv": b"x"}) == {"req": "a.csv"}
    assert map_inputs(c, {"a.pdf": b"x"}) is None
    assert map_inputs(c, {"a.csv": b""}) is None
    assert map_inputs(c, {"a.csv": b"x", "b.pdf": b"y"}) == {"req": "a.csv", "opt": "b.pdf"}


def test_resolve_roles_unit():
    from medha.store.models import resolve_roles

    assert resolve_roles(["date", "item", "category", "amount"]) == {
        "amount": "amount",
        "category": "category",
        "date": "date",
        "item": "item",
    }
    assert resolve_roles(["day", "product", "cat", "total_usd"]) == {
        "amount": "total_usd",
        "category": "cat",
        "date": "day",
        "item": "product",
    }
    # POS exports call revenue "sales": top_v2.csv (product,sales) must
    # resolve the amount role so the probe HITs instead of repairing.
    assert resolve_roles(["product", "sales"]) == {"amount": "sales", "item": "product"}
    assert resolve_roles(["date", "item", "category", "gross_amount"])["amount"] == "gross_amount"
    assert "category" not in resolve_roles(["date", "item", "kind", "total"])
    assert resolve_roles(["vendor", "amount", "memo"]) == {"amount": "amount"}


def test_map_inputs_prefers_exact_logical_name():
    """Already-mapped (logical-keyed) inputs pass through untouched.

    The repair-on-failure path hands the gate logical keys; without the
    exact-name preference those extensionless keys fail format matching.
    """
    c = SkillContract(
        skill_id="s",
        version=1,
        intent="t",
        inputs={"req": {"type": "file", "formats": ["csv"]}},
    )
    assert map_inputs(c, {"req": b"x"}) == {"req": "req"}
    assert map_inputs(c, {"req": b"x", "a.csv": b"y"}) == {"req": "req"}
