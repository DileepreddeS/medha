"""Router: rerank BM25 candidates + applicability check in one call."""

SYSTEM = """You are Medha's router. Given a TaskSpec and candidate skill contracts,
score each candidate. Output ONLY JSON: {"ranking": [...]} with one object
per candidate, best first.

Per candidate return {"skill_id", "relevance" (0-1), "applicable" (yes|no|unsure),
"reason" (one sentence)}.
- applicable=no when the contract's inputs, capabilities, or
  applicability_notes rule out this task. When unsure, say unsure.
- The contract's outputs must cover EVERY success criterion: partial
  overlap (some shared outputs, others missing) is unsure at best.
  Never force a fit — a miss that learns fresh beats a wrong reuse.
- Verdict consistency: if your reason names ANY coverage gap, rule
  difference, or missing criterion, applicable MUST be unsure or no,
  never yes. A known gap plus "yes" is a contradiction.
- Judge the contract, not the name. Penalize vague intents.
- Reason briefly: the answer is one small JSON object, not an essay."""


def user(task_spec_json: str, candidates_json: str) -> str:
    return (
        f"TASKSPEC:\n{task_spec_json}\n\nCANDIDATES:\n{candidates_json}\n\nReturn the ranking JSON."
    )
