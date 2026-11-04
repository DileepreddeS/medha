"""Test writer (ISOLATED): contract + samples + task goal -> test suite.

This module only ever sees the contract, input samples, and the task
goal — never the author's code or reasoning. The goal is planner
output (task data, like the judge's copy), so independence holds.
Called twice per candidate (visible, holdout).
"""

from __future__ import annotations

from typing import Any

from medha.llm.client import ChatMessage
from medha.llm.prompts import testwriter as prompts
from medha.store.models import SkillContract, TestSuiteOutput


def write_tests(
    llm: Any,
    contract: SkillContract,
    samples: str,
    suite: str,
    task_spec: Any = None,
) -> str:
    assert suite in ("visible", "holdout")
    task_block = ""
    if task_spec is not None:
        criteria = "\n".join(f"- {c}" for c in (task_spec.success_criteria or []))
        task_block = f"goal: {task_spec.goal}\n{criteria}"[:1500]
    out = llm.complete_json(
        "testwriter",
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(
                role="user",
                content=prompts.user(contract.model_dump_json(), samples, suite, task_block),
            ),
        ],
        TestSuiteOutput,
    )
    assert isinstance(out, TestSuiteOutput)
    return out.tests_py
