"""Testgen: the task goal reaches the testwriter prompt."""

from medha.store.models import SkillContract, TaskSpec, TestSuiteOutput
from medha.verify import testgen


class _CapLLM:
    def __init__(self):
        self.messages = None

    def complete_json(self, role, messages, schema):
        assert role == "testwriter"
        self.messages = messages
        return TestSuiteOutput(tests_py="def test_x(): pass")


def _contract():
    return SkillContract(
        skill_id="s",
        version=1,
        intent="x",
        inputs={"in_csv": {"type": "file", "formats": ["csv"]}},
        postconditions_deterministic=["output result exists"],
    )


def test_write_tests_includes_task_goal():
    """Regression (top_v1 escape): tests tested the author's contract,
    which declared result-as-path; the goal prescribing Return{top}
    never reached the testwriter, so the suite blessed the file and
    the gate passed an empty top."""
    llm = _CapLLM()
    spec = TaskSpec(goal='Return {"top": [...]}', success_criteria=["len(top) == 2"])
    out = testgen.write_tests(llm, _contract(), "samples", "visible", spec)
    assert out == "def test_x(): pass"
    user_text = llm.messages[1].content
    assert "what the user actually asked" in user_text
    assert 'Return {"top": [...]}' in user_text
    assert "len(top) == 2" in user_text


def test_write_tests_without_spec_sends_no_task_block():
    llm = _CapLLM()
    testgen.write_tests(llm, _contract(), "samples", "holdout")
    assert "what the user actually asked" not in llm.messages[1].content
