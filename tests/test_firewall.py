"""Evaluator firewall (AGENTS.md rule 2, spec §3.5): author-side code must never
read, import, or modify the holdout store, the judge, or bench checkers."""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUTHOR_SIDE_DIRS = ["medha/core", "medha/llm", "medha/sandbox", "medha/security", "medha/store"]
AUTHOR_SIDE_FILES = ["medha/config.py", "medha/smoke.py", "medha/glimmer.py"]

FORBIDDEN_IMPORT_PREFIXES = (
    "medha.verify.holdout",
    "medha.verify.judge",
    "bench.checkers",
)
FORBIDDEN_SUBSTRINGS = (
    "MEDHA_VERIFY_DIR",
    "verify_store",
    "verify.holdout",
    "verify.judge",
    "bench.checkers",
    "bench/checkers",
)


def _iter_files(exclude=()):
    for d in AUTHOR_SIDE_DIRS:
        p = REPO / d
        if p.exists():
            yield from sorted(p.rglob("*.py"))
    for f in AUTHOR_SIDE_FILES:
        if f in exclude:
            continue
        p = REPO / f
        if p.exists():
            yield p
    api = REPO / "medha" / "api"
    if api.exists():
        yield from sorted(api.rglob("*.py"))


def _imported_modules(tree: ast.AST) -> list[str]:
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_no_firewalled_imports():
    violations = []
    for path in _iter_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for mod in _imported_modules(tree):
            if mod.startswith(FORBIDDEN_IMPORT_PREFIXES):
                violations.append(f"{path.relative_to(REPO)} imports {mod}")
    assert not violations, violations


def test_no_firewalled_path_references():
    violations = []
    # medha/config.py only DECLARES the env var name; it never reads the store.
    for path in _iter_files(exclude=("medha/config.py",)):
        text = path.read_text()
        for sub in FORBIDDEN_SUBSTRINGS:
            if sub in text:
                violations.append(f"{path.relative_to(REPO)} references {sub!r}")
    assert not violations, violations


def test_verifier_side_does_not_leak_into_author_prompts():
    llm_init = (REPO / "medha" / "llm" / "__init__.py").read_text()
    assert "verify" not in llm_init
