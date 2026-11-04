"""Judge (Muse Spark 1.3 via Meta API): semantic postconditions -> verdict.

FIREWALLED: only medha.verify.gate may use this module. The judge sees the
contract, input samples, and outputs — never the author's reasoning.
"""

from __future__ import annotations

import json
from typing import Any

from medha.llm.client import ChatMessage
from medha.llm.prompts import judge as prompts
from medha.store.models import JudgeOutput, SkillContract


def judge(
    llm: Any,
    contract: SkillContract,
    result: dict[str, Any],
    files_text: dict[str, str],
    samples: str = "",
    task_spec: Any = None,
) -> JudgeOutput:
    if not contract.postconditions_semantic:
        return JudgeOutput(verdicts=[], overall_pass=True)
    outputs = json.dumps({"result": result, "files": files_text}, default=str)[:6000]
    task_block = ""
    if task_spec is not None:
        criteria = "\n".join(f"- {c}" for c in (task_spec.success_criteria or []))
        task_block = f"goal: {task_spec.goal}\n{criteria}"[:1500]
    out = llm.complete_json(
        "judge",
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(
                role="user",
                content=prompts.user(
                    contract.model_dump_json(),
                    outputs,
                    "\n".join(contract.postconditions_semantic),
                    samples[:4000],
                    task_block,
                ),
            ),
        ],
        JudgeOutput,
    )
    assert isinstance(out, JudgeOutput)
    return out
