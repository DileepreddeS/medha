"""Deterministic postconditions: compiled to checks by the verify module.

Supported forms (strict — anything else FAILS, never silently passes):
- "L == R ± tol"   approximate numeric equality
- "L == R", "L != R", "L > R", "L < R", "L >= R", "L <= R"
- "every ITEM in COLL appears in FILE"
- "output NAME exists"

Expressions evaluate over result keys + output file texts with a safe
AST subset (no attribute calls except .keys/.values/.items, no imports).
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Any

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_FUNCS: dict[str, Any] = {
    "sum": sum,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
}
_CMPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_EVERY_RE = re.compile(r"^every\s+(.+?)\s+in\s+(.+?)\s+appears\s+in\s+(.+?)$", re.I)
_OUTPUT_RE = re.compile(r"^output\s+(\S+)\s+exists$", re.I)
_CMP_RE = re.compile(r"^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+?)(?:\s*±\s*([\d.]+))?$")


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, ctx)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise ValueError(f"unknown variable {node.id!r}")
        return ctx[node.id]
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, ctx)
        if isinstance(base, dict) and node.attr in base:
            return base[node.attr]
        raise ValueError(f"bad attribute {node.attr!r}")
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        return base[key]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, ctx), _eval(node.right, ctx))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _eval(node.operand, ctx)
        return val if isinstance(node.op, ast.UAdd) else -val
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[_eval(a, ctx) for a in node.args])
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in ("keys", "values", "items")
            and not node.args
            and not node.keywords
        ):
            base = _eval(node.func.value, ctx)
            if isinstance(base, dict):
                return getattr(base, node.func.attr)()
        raise ValueError("disallowed call")
    if isinstance(node, ast.List):
        return [_eval(e, ctx) for e in node.elts]
    raise ValueError(f"disallowed expression {type(node).__name__}")


def eval_expr(expr: str, ctx: dict[str, Any]) -> Any:
    return _eval(ast.parse(expr, mode="eval"), ctx)


def texts_of(files: dict[str, bytes], limit: int = 200_000) -> dict[str, str]:
    return {k: v.decode("utf-8", errors="replace") for k, v in files.items() if len(v) <= limit}


def _resolve_text(value: Any, files_text: dict[str, str]) -> str | None:
    if not isinstance(value, str):
        return None
    if value in files_text:
        return files_text[value]
    base = value.rsplit("/", 1)[-1]
    if base in files_text:
        return files_text[base]
    return value  # treat as literal text


def check_one(post: str, result: dict[str, Any], files_text: dict[str, str]) -> dict[str, Any]:
    ctx: dict[str, Any] = {**result, **files_text, "result": result}
    row = {"postcondition": post, "passed": False, "detail": ""}
    try:
        m = _OUTPUT_RE.match(post.strip())
        if m:
            name = m.group(1)
            ok = name in files_text or any(k.endswith("/" + name) for k in files_text)
            if not ok and name in result and isinstance(result[name], str):
                ref = result[name]
                ok = ref in files_text or ref.rsplit("/", 1)[-1] in files_text
            row.update(passed=ok, detail=f"output {name!r} {'found' if ok else 'missing'}")
            return row
        m = _EVERY_RE.match(post.strip())
        if m:
            coll = _eval(ast.parse(m.group(2), mode="eval"), ctx)
            items = list(coll.keys()) if isinstance(coll, dict) else list(coll)
            text = _resolve_text(_eval(ast.parse(m.group(3), mode="eval"), ctx), files_text)
            missing = [str(i) for i in items if text is None or str(i) not in text]
            row.update(
                passed=not missing,
                detail=f"{len(items) - len(missing)}/{len(items)} present",
            )
            return row
        m = _CMP_RE.match(post.strip())
        if m:
            left, op, right, tol = m.group(1), m.group(2), m.group(3), m.group(4)
            lv, rv = eval_expr(left, ctx), eval_expr(right, ctx)
            if tol is not None:
                if op != "==":
                    raise ValueError("± tolerance only valid with ==")
                ok = abs(float(lv) - float(rv)) <= float(tol)
                row.update(passed=ok, detail=f"{lv!r} vs {rv!r} ± {tol}")
            else:
                fn = {
                    "==": operator.eq,
                    "!=": operator.ne,
                    ">": operator.gt,
                    "<": operator.lt,
                    ">=": operator.ge,
                    "<=": operator.le,
                }[op]
                row.update(passed=bool(fn(lv, rv)), detail=f"{lv!r} {op} {rv!r}")
            return row
        row["detail"] = "unsupported postcondition form"
        return row
    except Exception as e:
        row["detail"] = f"check error: {e}"
        return row


def check_all(
    result: dict[str, Any], files: dict[str, bytes], postconditions: list[str]
) -> list[dict[str, Any]]:
    files_text = texts_of(files)
    return [check_one(p, result, files_text) for p in postconditions]


_KNOWN_SHAPES = frozenset(
    {
        "file",
        "path",
        "string",
        "str",
        "text",
        "list",
        "dict",
        "number",
        "int",
        "integer",
        "float",
        "boolean",
        "bool",
    }
)


def _shape_ok(want: str, value: Any) -> bool:
    w = want.strip().lower()
    if w in ("file", "path", "string", "str", "text"):
        return isinstance(value, str)
    if w in ("list",):
        return isinstance(value, list)
    if w in ("dict",):
        return isinstance(value, dict)
    if w in ("number", "int", "integer", "float"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if w in ("boolean", "bool"):
        return isinstance(value, bool)
    raise ValueError(f"unknown spec type {want!r}")


def check_spec_shape(result: dict[str, Any], outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Machine-check planner-declared result keys and value types.

    The contract's postconditions are author-written, so a skill that
    returns a path string where the goal prescribes a list can bless
    itself (top_v1 escape). The planner's outputs come from the goal
    instead: every required key must be present with a conforming
    type. Optional keys may be absent; unknown type names are skipped
    (never fail on planner novelty).
    """
    rows: list[dict[str, Any]] = []
    for key in sorted(outputs):
        spec = outputs[key]
        optional = bool(getattr(spec, "optional", False))
        want = str(getattr(spec, "type", "") or "")
        if want.strip().lower() not in _KNOWN_SHAPES:
            continue  # never fail on planner novelty
        if key not in result:
            if optional:
                continue
            rows.append(
                {"postcondition": f"result has {key}", "passed": False, "detail": "missing"}
            )
            continue
        ok = _shape_ok(want, result[key])
        rows.append(
            {
                "postcondition": f"result['{key}'] is {want}",
                "passed": ok,
                "detail": f"got {type(result[key]).__name__}",
            }
        )
    return rows


def match_expected(
    result: dict[str, Any], files: dict[str, bytes], expected: dict[str, Any]
) -> list[str]:
    """Replay check: result subset equality + expected files present."""
    failures: list[str] = []

    def _subset(want: Any, got: Any, path: str) -> None:
        if isinstance(want, dict):
            if not isinstance(got, dict):
                failures.append(f"{path}: not an object")
                return
            for k, v in want.items():
                if k not in got:
                    failures.append(f"{path}.{k}: missing")
                else:
                    _subset(v, got[k], f"{path}.{k}")
        elif isinstance(want, list):
            if list(got or []) != want:
                failures.append(f"{path}: list differs")
        elif isinstance(want, float):
            if abs(float(got) - want) > 1e-6:
                failures.append(f"{path}: {got!r} != {want!r}")
        elif got != want:
            failures.append(f"{path}: {got!r} != {want!r}")

    _subset(expected.get("result_subset", {}), result, "result")
    for name in expected.get("files_present", []):
        if name not in files:
            failures.append(f"file {name!r} missing")
    return failures
