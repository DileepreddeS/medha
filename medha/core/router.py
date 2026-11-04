"""Router: BM25 over full skill text → LLM rerank+applicability → Beta pick.

Ranks by relevance × E[p_success] − λ·norm_cost (spec §3.3). A cheap
precondition probe (contract inputs vs actual files) runs before any hit is
returned; probe failure routes to repair/learn, never a silent wrong answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # offline envs: equivalent vendored fallback
    from medha.core._bm25 import BM25Okapi

from medha.core import library
from medha.llm.client import ChatMessage
from medha.llm.prompts import router as prompts
from medha.store.db import Database
from medha.store.events import log_event
from medha.store.models import (
    RoutingOutput,
    SkillContract,
    TaskSpec,
    csv_headers,
    resolve_roles,
)
from medha.store.models import (
    map_inputs as _map_inputs,
)

LAMBDA_COST = 0.1

# Relevance floor for honest partials ("unsure" + gap markers). Below
# route_min on purpose: partial coverage is inherently lower-relevance,
# and the composer (not the HIT path) consumes these candidates.
PARTIAL_MIN = 0.4


@dataclass
class RouteDecision:
    outcome: str  # hit | miss | compose
    skill_id: str | None = None
    version: int | None = None
    mapping: dict[str, str] = field(default_factory=dict)
    relevance: float = 0.0
    expected_p: float = 0.0
    reason: str = ""
    probe_failed: bool = False
    compose_ids: list[str] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _doc_for_skill(skill_md: str, contract: SkillContract, skill_py: str) -> str:
    return "\n".join([skill_md, contract.intent, " ".join(contract.preconditions), skill_py])


def map_inputs(contract: SkillContract, files: dict[str, bytes]) -> dict[str, str] | None:
    """Map contract logical inputs to actual files. None = no match."""
    return _map_inputs(contract.inputs, files)


def expected_p(alpha: float, beta: float) -> float:
    return (alpha + 1) / (alpha + beta + 2)


# Contrastive markers: when the reranker's own reason names a coverage
# gap while voting anything but "no", the verdict contradicts the rationale. Observed
# forced-HIT reasons all carry one ("hardcodes Whole Foods/Shell instead
# of required Trader Joes/BP", "does not mention date range"); confident
# HIT reasons state plain matches. A downgrade costs a fresh learn, while
# a forced HIT fails the task — so the tie breaks toward miss.
GAP_MARKERS = (
    "instead of",
    "rather than",
    "does not",
    "doesn't",
    "do not",
    "don't",
    "missing",
    "lacks",
    "fails to",
    "no mention",
    "not mention",
    "never ",
    "without ",
    "hardcodes",
    "wrong",
    "omit",
)


def contradicts_yes(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in GAP_MARKERS)


def schema_probe(view: Any, mapping: dict[str, str], files: dict[str, bytes]) -> str | None:
    """Re-resolve recorded column roles on the current inputs.

    Returns a detail string when a recorded role no longer resolves
    (schema drift → repair), else None. Skills without a role record
    (legacy packages, non-tabular inputs) skip the probe.
    """
    recorded = (view.provenance or {}).get("input_roles") or {}
    if not recorded:
        return None
    for logical, fname in mapping.items():
        want = recorded.get(logical)
        if not want:
            continue
        headers = csv_headers(files[fname])
        if headers is None:
            continue  # unreadable now; execution fails honestly instead
        have = resolve_roles(headers)
        missing = [role for role in want if role not in have]
        if missing:
            return f"columns for roles {missing} not found in {fname} (headers: {headers})"
    return None


def route(
    *,
    db: Database,
    llm: Any,
    skills_dir: str,
    task_id: str,
    task_spec: TaskSpec,
    files: dict[str, bytes],
    route_min: float,
) -> RouteDecision:
    rows = library.list_skills(db)
    if not rows:
        return RouteDecision(outcome="miss", reason="empty library")
    items = [(r, library.read_package(skills_dir, r["id"], r["active_version"])) for r in rows]
    docs = [_tokens(_doc_for_skill(v.skill_md, v.contract, v.skill_py)) for _, v in items]
    query = _tokens(task_spec.goal + " " + " ".join(task_spec.success_criteria))
    scores = BM25Okapi(docs).get_scores(query)
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[:8]
    top = [items[i] for i in order]

    cand_json = json.dumps(
        [
            {"skill_id": r["id"], "contract": json.loads(v.contract.model_dump_json())}
            for r, v in top
        ]
    )
    ranking = llm.complete_json(
        "router",
        [
            ChatMessage(role="system", content=prompts.SYSTEM),
            ChatMessage(role="user", content=prompts.user(task_spec.model_dump_json(), cand_json)),
        ],
        RoutingOutput,
    )
    assert isinstance(ranking, RoutingOutput)
    last = getattr(llm, "last", None)
    usage = (
        {"tokens_in": last.tokens_in, "tokens_out": last.tokens_out, "cost_usd": last.cost_usd}
        if last
        else {}
    )
    by_id = {r.skill_id: r for r in ranking.ranking}
    scored = []
    downgraded: list[str] = []
    partials: list[tuple[float, dict[str, Any]]] = []
    votes: dict[str, dict[str, object]] = {}
    for r, v in top:
        item = by_id.get(r["id"])
        rel = float(item.relevance) if item else 0.0
        votes[r["id"]] = {
            "applicable": item.applicable if item else "unranked",
            "relevance": rel,
            "reason": item.reason if item else "",
        }
        if item and item.applicable == "no":
            continue
        if item and contradicts_yes(item.reason):
            # A gap-naming vote can never HIT, but an "unsure" one is an
            # honest partial: it may still compose, never hit alone.
            downgraded.append(r["id"])
            if item.applicable == "unsure" and rel >= PARTIAL_MIN:
                partials.append((rel, r))
            continue
        ep = expected_p(float(r["alpha"]), float(r["beta"]))
        score = rel * ep - LAMBDA_COST * min(float(r["cost_ema"] or 0), 1.0)
        scored.append((score, rel, ep, r, v, item.reason if item else "unranked"))
    log_event(
        db,
        type="route.candidates",
        actor="router",
        task_id=task_id,
        payload={
            "bm25_order": [r["id"] for r, _ in top],
            "scored": [s[3]["id"] for s in scored],
            "downgraded": downgraded,
            "partials": [r["id"] for _, r in partials],
            "votes": votes,
        },
        **usage,
    )
    if not scored and not partials:
        return RouteDecision(outcome="miss", reason="all candidates inapplicable")
    if not scored:
        if len(task_spec.sub_goals) >= 2 and len(partials) >= 2:
            ids = [r["id"] for _, r in sorted(partials, key=lambda t: -t[0])[:3]]
            return RouteDecision(
                outcome="compose",
                reason="multi-goal task, partial skills cover sub-goals",
                compose_ids=ids,
            )
        return RouteDecision(outcome="miss", reason="only partial coverage")
    scored.sort(key=lambda s: -s[0])
    _, rel, ep, best_row, best_view, reason = scored[0]
    if rel < route_min:
        return RouteDecision(outcome="miss", reason=f"best relevance {rel:.2f} < {route_min}")
    strong = [s for s in scored if s[1] >= route_min]
    pool = [(s[1], s[3]) for s in strong] + partials
    if len(task_spec.sub_goals) >= 2 and len(pool) >= 2:
        ids = [r["id"] for _, r in sorted(pool, key=lambda t: -t[0])[:3]]
        return RouteDecision(
            outcome="compose",
            reason="multi-goal task, skills cover sub-goals",
            compose_ids=ids,
        )
    mapping = map_inputs(best_view.contract, files)
    if mapping is None:
        return RouteDecision(
            outcome="miss",
            skill_id=best_row["id"],
            version=best_row["active_version"],
            relevance=rel,
            expected_p=ep,
            reason="precondition probe failed: inputs do not match contract",
            probe_failed=True,
        )
    schema_detail = schema_probe(best_view, mapping, files)
    if schema_detail is not None:
        return RouteDecision(
            outcome="miss",
            skill_id=best_row["id"],
            version=best_row["active_version"],
            relevance=rel,
            expected_p=ep,
            reason=f"precondition probe failed: {schema_detail}",
            probe_failed=True,
        )
    return RouteDecision(
        outcome="hit",
        skill_id=best_row["id"],
        version=best_row["active_version"],
        mapping=mapping,
        relevance=rel,
        expected_p=ep,
        reason=reason,
    )
