"""Author: TaskSpec -> skill draft (code + doc + contract)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medha.llm.client import ChatMessage
from medha.llm.prompts import author as prompts
from medha.store.models import AuthorOutput, SkillContract, TaskSpec


@dataclass
class AuthorDraft:
    skill_py: str
    skill_md: str
    contract: SkillContract


def draft_skill(
    llm: Any,
    role: str,
    task_spec: TaskSpec,
    samples: str,
    lessons: str = "",
) -> AuthorDraft:
    """Draft a skill. role is "author" (Glimmer) or "escalation" (Spark 1.3)."""
    out = llm.complete_json(
        role,
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(
                role="user",
                content=prompts.user(task_spec.model_dump_json(), task_spec.goal, samples, lessons),
            ),
        ],
        AuthorOutput,
    )
    assert isinstance(out, AuthorOutput)
    return AuthorDraft(skill_py=out.skill_py, skill_md=out.skill_md, contract=out.contract)
