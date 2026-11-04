"""Planner: user task + file schemas -> TaskSpec."""

from __future__ import annotations

from typing import Any

from medha.llm.client import ChatMessage
from medha.llm.prompts import planner as prompts
from medha.store.models import TaskSpec


def plan(llm: Any, task_text: str, file_schemas: dict[str, str]) -> TaskSpec:
    schema_block = "\n\n".join(f"--- {name} ---\n{s}" for name, s in file_schemas.items())
    out = llm.complete_json(
        "planner",
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(role="user", content=prompts.user(task_text, schema_block or "(no files)")),
        ],
        TaskSpec,
    )
    assert isinstance(out, TaskSpec)
    return out
