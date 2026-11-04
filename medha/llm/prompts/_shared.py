"""Shared prompt fragments. Single source so prompts can't drift from schemas."""

CONTRACT_SHAPE = """The "contract" object must use EXACTLY these keys
(* = required; comments show meaning, do not emit them):
{
  "skill_id": "snake_case_id",         // * unique id for this skill
  "version": 1,
  "intent": "one-line capability",     // * what the skill does
  "inputs": {"logical_name": {"type": "file", "formats": ["csv"]}},
  "outputs": {"name": {"type": "file", "formats": ["md"]}},
  "preconditions": ["facts that must hold of the inputs"],
  "postconditions_deterministic": ["machine-checked forms only (see rules)"],
  "postconditions_semantic": ["claims the judge verifies from outputs"],
  "applicability_notes": "when this skill does NOT apply"
}"""
