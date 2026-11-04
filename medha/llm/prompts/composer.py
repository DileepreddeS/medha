"""Composer: TaskSpec + child skills -> composite skill.py + contract JSON."""

from medha.llm.prompts._shared import CONTRACT_SHAPE

SYSTEM = f"""You are Medha's composer. Given a TaskSpec with sub-goals and the
contracts (plus run signatures) of existing verified skills, compose them
into ONE thin composite skill. Output ONLY JSON: {{"skill_py", "contract"}}.

{CONTRACT_SHAPE}

Composite rules:
- The composite skill.py calls child run() functions in DAG order with
  typed handoffs (output keys -> input keys). Children are available as
  modules child_0, child_1, ... (import child_0). No other imports beyond
  the skill allowlist + pathlib.
- Union child preconditions; add end-to-end postconditions for the TaskSpec.
- Same sandbox rules: deterministic, no network, JSON-serializable return.
- Never inline child logic: call the children."""


def user(task_spec_json: str, children_json: str) -> str:
    return (
        f"TASKSPEC:\n{task_spec_json}\n\n"
        f"CHILD SKILLS (contracts + I/O keys):\n{children_json}\n\n"
        "Return the composite JSON."
    )
