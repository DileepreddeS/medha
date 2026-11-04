"""Gate stage 1: host-side static checks. No code is executed here."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from medha.security.scan import Finding, scan_code, scan_markdown
from medha.store.models import SkillContract

KNOWN_CAPABILITIES = frozenset({"read_input", "write_output", "network", "side_effect"})
TRUST_LEVELS = frozenset({"candidate", "verified", "trusted"})


@dataclass
class StaticReport:
    passed: bool
    findings: list[Finding] = field(default_factory=list)


def _has_run_fn(skill_py: str) -> bool:
    try:
        tree = ast.parse(skill_py)
    except SyntaxError:
        return False
    return any(
        isinstance(n, ast.FunctionDef) and n.name == "run" and len(n.args.args) == 2
        for n in ast.walk(tree)
    )


def static_check(
    skill_py: str,
    skill_md: str,
    contract: SkillContract,
    extra_imports: frozenset[str] = frozenset(),
) -> StaticReport:
    findings: list[Finding] = []
    findings.extend(scan_code(skill_py, extra_imports))
    findings.extend(scan_markdown(skill_md))
    if not _has_run_fn(skill_py):
        findings.append(Finding("entrypoint", "skill.py must define run(inputs, workdir)"))
    if not contract.skill_id or "/" in contract.skill_id or "\\" in contract.skill_id:
        findings.append(Finding("contract", "skill_id is empty or contains path separators"))
    if contract.version < 1:
        findings.append(Finding("contract", "version must be >= 1"))
    unknown = [c for c in contract.capabilities if c not in KNOWN_CAPABILITIES]
    if unknown:
        findings.append(Finding("contract", f"unknown capabilities: {unknown}"))
    if contract.trust not in TRUST_LEVELS:
        findings.append(Finding("contract", f"unknown trust level {contract.trust!r}"))
    if not contract.postconditions_deterministic and not contract.postconditions_semantic:
        findings.append(Finding("contract", "contract has no postconditions to check"))
    return StaticReport(passed=not findings, findings=findings)
