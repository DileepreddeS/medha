"""Static scanners for generated skill code and SKILL.md (spec §3.5.1)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

ALLOWED_IMPORTS = frozenset(
    {
        "pandas",
        "numpy",
        "openpyxl",
        "pdfplumber",
        "pypdf",
        "matplotlib",
        "json",
        "csv",
        "re",
        "math",
        "statistics",
        "datetime",
        "pathlib",
        "decimal",
        "collections",
        "itertools",
        "typing",
        "dataclasses",
    }
)

BANNED_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
BANNED_ATTRS = frozenset(
    {
        "system",
        "popen",
        "execv",
        "execve",
        "spawnl",
        "spawnv",
        "environ",
        "getenv",
        "getpass",
    }
)
MAX_CODE_BYTES = 100_000

_SECRET_RES = [
    re.compile(r"LLM\|\d+\|[A-Za-z0-9._-]+"),
    re.compile(r"hf_[A-Za-z0-9]+"),
    re.compile(r"sk-[A-Za-z0-9-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)(api[_-]?key|bearer|hf_token|meta_api_key|medha_token|glimmer_api_key)"
        r"\s*[:=]\s*\S+"
    ),
]

_MD_RES = [
    (re.compile(r"(?i)ignore (all |any )?previous instructions"), "instruction-override"),
    (re.compile(r"(?i)\b(you are now|new role|system prompt|jailbreak)\b"), "role-impersonation"),
    (re.compile(r"!\[[^\]]*\]\(https?://"), "image-exfil"),
    (re.compile(r"https?://\S+"), "url"),
    (re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"), "base64-blob"),
    (re.compile(r"[\u200b-\u200f\ufeff]"), "unicode-smuggling"),
    (re.compile(r"(?i)do not (tell|show|reveal|mention)"), "secrecy-plea"),
]


@dataclass
class Finding:
    rule: str
    message: str
    line: int | None = None


def _is_abs_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value.startswith(("/", "~")) or ".." in node.value.split("/"))
    )


class _CodeVisitor(ast.NodeVisitor):
    def __init__(self, extra_allowed: frozenset[str] = frozenset()) -> None:
        self.findings: list[Finding] = []
        self.extra_allowed = extra_allowed

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_IMPORTS and top not in self.extra_allowed:
                self.findings.append(
                    Finding("import", f"import of {alias.name!r} is not allowlisted", node.lineno)
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level or not node.module:
            self.findings.append(Finding("import", "relative imports are forbidden", node.lineno))
            return
        top = node.module.split(".")[0]
        if top not in ALLOWED_IMPORTS and top not in self.extra_allowed:
            self.findings.append(
                Finding("import", f"import from {node.module!r} is not allowlisted", node.lineno)
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id in BANNED_CALLS:
                self.findings.append(Finding("call", f"{fn.id}() is forbidden", node.lineno))
            elif fn.id == "open" and node.args and _is_abs_literal(node.args[0]):
                self.findings.append(
                    Finding("path", "open() of absolute/parent paths is forbidden", node.lineno)
                )
            elif fn.id == "Path" and node.args and _is_abs_literal(node.args[0]):
                self.findings.append(
                    Finding("path", "absolute Path() literals are forbidden", node.lineno)
                )
        elif isinstance(fn, ast.Attribute):
            if fn.attr in BANNED_ATTRS:
                self.findings.append(Finding("call", f".{fn.attr}() is forbidden", node.lineno))
            if fn.attr == "home" and isinstance(fn.value, ast.Name) and fn.value.id == "Path":
                self.findings.append(Finding("path", "Path.home() is forbidden", node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in BANNED_ATTRS and isinstance(node.value, ast.Name):
            self.findings.append(Finding("attr", f".{node.attr} access is forbidden", node.lineno))
        self.generic_visit(node)


def scan_code(code: str, extra_allowed: frozenset[str] = frozenset()) -> list[Finding]:
    findings: list[Finding] = []
    if len(code.encode()) > MAX_CODE_BYTES:
        findings.append(Finding("size", f"skill.py exceeds {MAX_CODE_BYTES} bytes"))
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [Finding("syntax", f"does not parse: {e}", getattr(e, "lineno", None))]
    visitor = _CodeVisitor(extra_allowed)
    visitor.visit(tree)
    findings.extend(visitor.findings)
    for rx in _SECRET_RES:
        if rx.search(code):
            findings.append(Finding("secret", f"possible embedded secret ({rx.pattern[:24]}…)"))
    return findings


def scan_markdown(md: str) -> list[Finding]:
    findings: list[Finding] = []
    for rx, rule in _MD_RES:
        for m in rx.finditer(md):
            line = md.count("\n", 0, m.start()) + 1
            findings.append(Finding(rule, f"matched {m.group(0)[:48]!r}", line))
            if len(findings) >= 20:
                return findings
    return findings
