"""Every role prompt must name the exact JSON keys its schema requires.

Regression guard: the planner prompt once said "matching the requested
schema" without ever showing it, and the live model improvised sub-goal
keys (id/desc/order/goal) that failed TaskSpec validation. Schemas ignore
extra keys, so the contract is: required keys must appear in the prompt.
"""

from medha.llm import prompts
from medha.llm.prompts import _shared


def test_planner_names_taskspec_keys():
    for key in (
        '"goal"',
        '"inputs"',
        '"outputs"',
        '"success_criteria"',
        '"sub_goals"',
        '"files"',
        '"name"',
        '"needs"',
        '"gives"',
        '"formats"',
    ):
        assert key in prompts.planner.SYSTEM, key


def test_author_and_composer_share_contract_shape():
    assert _shared.CONTRACT_SHAPE in prompts.author.SYSTEM
    assert _shared.CONTRACT_SHAPE in prompts.composer.SYSTEM
    for key in (
        '"skill_id"',
        '"intent"',
        '"inputs"',
        '"postconditions_deterministic"',
        '"postconditions_semantic"',
        '"applicability_notes"',
    ):
        assert key in _shared.CONTRACT_SHAPE, key


def test_author_demands_role_based_columns():
    """The author must resolve CSV columns by role, not exact header.

    Regression: a learned skill hardcoded "category"/"amount" and died
    with ValueError on the reuse variant ("cat"/"total_usd"), forcing a
    repair where pure reuse should have worked.
    """
    for snippet in ("by ROLE", "amount", "total_usd", "fall back to shape"):
        assert snippet in prompts.author.SYSTEM, snippet


def test_author_prompt_matches_role_table():
    """Every ROLE_ALIASES entry must appear in the author prompt.

    The probe (code table) and the skill (prompt table) must resolve
    roles identically, or the probe blesses inputs the skill mishandles.
    """
    from medha.store.models import ROLE_ALIASES

    for role, aliases in ROLE_ALIASES.items():
        assert role in prompts.author.SYSTEM, role
        for alias in aliases:
            assert alias in prompts.author.SYSTEM, alias


def test_router_names_ranking_wrapper():
    assert '"ranking"' in prompts.router.SYSTEM
    for key in ('"skill_id"', '"relevance"', '"applicable"', '"reason"'):
        assert key in prompts.router.SYSTEM, key


def test_judge_names_verdict_keys():
    for key in ("verdicts", "overall_pass", "postcondition", "quote", "pass"):
        assert key in prompts.judge.SYSTEM, key


def test_repairer_and_testwriter_name_keys():
    assert '"skill_py"' in prompts.repairer.SYSTEM
    assert '"notes"' in prompts.repairer.SYSTEM
    assert '"tests_py"' in prompts.testwriter.SYSTEM


def test_learned_prompt_guards_from_mini_c():
    """Prompt rules added after the first mini-C run (7/12).

    - Router forced fits (owner→sales HIT missing date coverage).
    - Author returned a file path where the goal asked for a list.
    - Tests blessed the output file while the result value was wrong.
    """
    assert "EVERY success criterion" in prompts.router.SYSTEM
    assert "Never force a fit" in prompts.router.SYSTEM
    assert "Verdict consistency" in prompts.router.SYSTEM
    assert "never yes" in prompts.router.SYSTEM
    assert "prescribed result schema EXACTLY" in prompts.author.SYSTEM
    assert "Data stays data" in prompts.author.SYSTEM
    assert "Import every module you reference" in prompts.author.SYSTEM
    assert "every Return key" in prompts.planner.SYSTEM
    assert "TYPE and shape of every result key" in prompts.testwriter.SYSTEM
    assert "never excuses a wrong result value" in prompts.testwriter.SYSTEM
    # top_v1 escape: the contract declared result-as-path, tests blessed the
    # file, judge leniency passed it. The goal now outranks the contract.
    assert "outrank the contract" in prompts.testwriter.SYSTEM
    assert "test the GOAL's schema" in prompts.testwriter.SYSTEM
    assert "Schema exactness" in prompts.judge.SYSTEM
