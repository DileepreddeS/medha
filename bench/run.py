"""MedhaBench runner. Usage: ``uv run python -m bench.run --config all``.

Configs: A (Glimmer baseline, no library), B (unverified self-made skills),
C (full Medha, cold library), repair (3 drift cases on C's library).
Writes bench/results/<timestamp>.json + PNG charts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bench.checkers import CHECKERS
from bench.pdfgen import make_pdf
from medha.core import author as author_mod
from medha.core import library, orchestrator, planner, reward, router
from medha.security.untrusted import schema_preview_csv, truncate
from medha.store.models import map_inputs


@dataclass
class TaskRow:
    id: str
    family: str
    passed: bool
    fails: list[str] = field(default_factory=list)
    tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    reused: bool = False
    detail: str = ""
    digest: str = ""  # output_digest(result, files); "" when nothing ran


def output_digest(result: dict[str, Any], files: dict[str, Any]) -> str:
    """Short stable hash of a task's outputs (result JSON + output files).

    Powers consistency comparisons (same task N×): equal outputs have
    equal digests regardless of run, config, or library state.
    """
    h = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode())
    names = sorted(files)
    for name in names:
        blob = files[name]
        h.update(name.encode())
        h.update(blob if isinstance(blob, bytes) else str(blob).encode())
    return h.hexdigest()[:16]


class Usage:
    def __init__(self) -> None:
        self.tokens = 0
        self.cost = 0.0
        self._seen: set[int] = set()

    def add(self, llm: Any) -> None:
        r = getattr(llm, "last", None)
        if r is not None and id(r) not in self._seen:
            self._seen.add(id(r))
            self.tokens += int(r.tokens_in) + int(r.tokens_out)
            self.cost += float(r.cost_usd)


def _report_row(row: TaskRow, task_id: str | None = None, db: Any = None) -> None:
    """Console line per task; failures also show the error + event sequence."""
    status = "PASS" if row.passed else "FAIL"
    print(
        f"[{status}] {row.id} tokens={row.tokens} cost=${row.cost_usd:.4f}"
        f" reused={row.reused} {row.latency_ms}ms",
        flush=True,
    )
    if row.passed:
        return
    for fail in row.fails:
        print(f"  error: {fail[:500]}", flush=True)
    if task_id is None or db is None:
        return
    try:
        types = [
            r["type"]
            for r in db.query("SELECT type FROM events WHERE task_id=? ORDER BY id", (task_id,))
        ]
    except Exception as e:
        print(f"  events: <unreadable: {e}>", flush=True)
        return
    seq = " -> ".join(types) if types else "(none)"
    print(f"  events: {seq}", flush=True)


def load_all(tasks_path: str | Path) -> dict[str, list[dict]]:
    doc = yaml.safe_load(Path(tasks_path).read_text())
    return {
        "tasks": doc.get("tasks", []),
        "repair": doc.get("repair", []),
        "heldout": doc.get("heldout", []),
        "compose": doc.get("compose", []),
    }


def load_suite(tasks_path: str | Path) -> tuple[list[dict], list[dict]]:
    doc = load_all(tasks_path)
    return doc["tasks"], doc["repair"]


def task_files(task: dict, fixtures_dir: str | Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for _logical, fname in task["files"].items():
        if fname.endswith(".pdf"):
            out[fname] = make_pdf(task.get("pdf_lines", []))
        else:
            out[fname] = (Path(fixtures_dir) / fname).read_bytes()
    return out


def routed_outcome(db: Any, task_id: str) -> str:
    row = db.query_one(
        "SELECT payload_json FROM events WHERE task_id=? AND type='task.routed'", (task_id,)
    )
    if not row:
        return ""
    try:
        return json.loads(row["payload_json"]).get("outcome", "")
    except json.JSONDecodeError:
        return ""


def _samples(files: dict[str, bytes]) -> str:
    return truncate(
        "\n\n".join(f"--- {n} ---\n{schema_preview_csv(b)}" for n, b in sorted(files.items())), 6000
    )


def run_config_a(
    tasks: list[dict], fixtures_dir: str | Path, llm: Any, runner: Any
) -> list[TaskRow]:
    """A: Glimmer + sandbox per task, no library, no gate."""
    rows = []
    for task in tasks:
        start = time.monotonic()
        usage = Usage()
        try:
            files = task_files(task, fixtures_dir)
            schemas = {n: schema_preview_csv(b) for n, b in files.items()}
            spec = planner.plan(llm, task["goal"], schemas)
            usage.add(llm)
            draft = author_mod.draft_skill(llm, "author", spec, _samples(files))
            usage.add(llm)
            mapping = map_inputs(draft.contract.inputs, files)
            digest = ""
            if mapping is None:
                fails = ["inputs do not match the drafted contract"]
            else:
                out = runner.run_skill(draft.skill_py, {k: files[v] for k, v in mapping.items()})
                fails = CHECKERS[task["family"]](
                    out.result if out.ok else {}, out.files, task["expected"]
                )
                if not out.ok:
                    fails = [f"run failed: {out.error[:200]}"]
                else:
                    digest = output_digest(out.result, out.files)
            rows.append(
                TaskRow(
                    task["id"],
                    task["family"],
                    not fails,
                    fails,
                    usage.tokens,
                    usage.cost,
                    int((time.monotonic() - start) * 1000),
                    digest=digest,
                )
            )
            _report_row(rows[-1])
        except Exception as e:  # bench must report, never crash
            rows.append(
                TaskRow(
                    task["id"],
                    task["family"],
                    False,
                    [f"harness: {e}"][:1],
                    usage.tokens,
                    usage.cost,
                    int((time.monotonic() - start) * 1000),
                )
            )
            _report_row(rows[-1])
    return rows


def run_config_b(
    tasks: list[dict], fixtures_dir: str | Path, db: Any, llm: Any, runner: Any, settings: Any
) -> list[TaskRow]:
    """B: Medha routing/reuse, but the gate is reduced to 'runs without error'."""
    from medha.core import learner as learner_mod

    rows = []
    for i, task in enumerate(tasks):
        start = time.monotonic()
        usage = Usage()
        task_id = f"bench-b-{i}-{task['id']}"
        try:
            files = task_files(task, fixtures_dir)
            schemas = {n: schema_preview_csv(b) for n, b in files.items()}
            spec = planner.plan(llm, task["goal"], schemas)
            usage.add(llm)
            decision = router.route(
                db=db,
                llm=llm,
                skills_dir=settings.MEDHA_SKILLS_DIR,
                task_id=task_id,
                task_spec=spec,
                files=files,
                route_min=settings.ROUTE_MIN,
            )
            usage.add(llm)
            if decision.outcome == "hit":
                assert decision.skill_id and decision.version
                view = library.read_package(
                    settings.MEDHA_SKILLS_DIR, decision.skill_id, decision.version
                )
                mapped = {k: files[v] for k, v in decision.mapping.items()}
                out = runner.run_skill(view.skill_py, mapped)
                fails = CHECKERS[task["family"]](
                    out.result if out.ok else {}, out.files, task["expected"]
                )
                reward.update(
                    db,
                    skill_id=decision.skill_id,
                    version=decision.version,
                    reward=1.0 if not fails else 0.0,
                    task_id=task_id,
                )
                rows.append(
                    TaskRow(
                        task["id"],
                        task["family"],
                        not fails,
                        fails,
                        usage.tokens,
                        usage.cost,
                        int((time.monotonic() - start) * 1000),
                        reused=True,
                        digest=output_digest(out.result, out.files) if out.ok else "",
                    )
                )
                _report_row(rows[-1], task_id, db)
                continue
            # unverified learn: author → runs? → register. No gate at all.
            draft = author_mod.draft_skill(llm, "author", spec, _samples(files))
            usage.add(llm)
            draft.contract.skill_id = learner_mod.unique_skill_id(
                db, learner_mod.slugify(spec.goal)
            )
            draft.contract.version = 1
            b_mapping = map_inputs(draft.contract.inputs, files)
            out = None
            if b_mapping is not None:
                out = runner.run_skill(draft.skill_py, {k: files[v] for k, v in b_mapping.items()})
            if out is not None and out.ok:
                library.stage_package(
                    settings.MEDHA_SKILLS_DIR,
                    draft.contract.skill_id,
                    1,
                    draft.skill_py,
                    draft.skill_md,
                    draft.contract,
                    {"source_task_id": task_id, "unverified": True},
                )
                library.activate(
                    db,
                    settings.MEDHA_SKILLS_DIR,
                    draft.contract.skill_id,
                    1,
                    "verified",
                    task_id=task_id,
                )
                reward.update(
                    db, skill_id=draft.contract.skill_id, version=1, reward=1.0, task_id=task_id
                )
            if out is None:
                fails = ["inputs do not match the drafted contract"]
            else:
                fails = CHECKERS[task["family"]](
                    out.result if out.ok else {}, out.files, task["expected"]
                )
                if not out.ok:
                    fails = [f"run failed: {out.error[:200]}"]
            rows.append(
                TaskRow(
                    task["id"],
                    task["family"],
                    not fails,
                    fails,
                    usage.tokens,
                    usage.cost,
                    int((time.monotonic() - start) * 1000),
                    digest=(
                        output_digest(out.result, out.files) if out is not None and out.ok else ""
                    ),
                )
            )
            _report_row(rows[-1], task_id, db)
        except Exception as e:
            rows.append(
                TaskRow(
                    task["id"],
                    task["family"],
                    False,
                    [f"harness: {e}"][:1],
                    usage.tokens,
                    usage.cost,
                    int((time.monotonic() - start) * 1000),
                )
            )
            _report_row(rows[-1], task_id, db)
    return rows


def run_config_c(
    tasks: list[dict],
    fixtures_dir: str | Path,
    db: Any,
    llm: Any,
    runner: Any,
    settings: Any,
    prefix: str = "bench-c",
) -> list[TaskRow]:
    """C: full Medha from an empty library, tasks in sequence (auto-approve)."""
    rows = []
    for i, task in enumerate(tasks):
        start = time.monotonic()
        usage = Usage()
        task_id = f"{prefix}-{i}-{task['id']}"
        try:
            files = task_files(task, fixtures_dir)
            res = orchestrator.run_task(
                db=db,
                llm=llm,
                runner=runner,
                settings=settings,
                task_id=task_id,
                task_text=task["goal"],
                files=files,
            )
            guard = 0
            while res.status == "needs_approval" and res.approval_id and guard < 3:
                res = orchestrator.decide_approval(
                    db=db,
                    llm=llm,
                    runner=runner,
                    settings=settings,
                    approval_id=res.approval_id,
                    decision="approve",
                )
                guard += 1
            # usage from the event log: every LLM call records tokens+cost
            spend = db.query_one(
                "SELECT COALESCE(SUM(tokens_in+tokens_out),0) t,"
                " COALESCE(SUM(cost_usd),0) c FROM events WHERE task_id=?",
                (task_id,),
            ) or {"t": 0, "c": 0.0}
            usage.tokens, usage.cost = int(spend["t"]), float(spend["c"])
            fails = CHECKERS[task["family"]](res.result, res.files, task["expected"])
            if res.status != "done":
                fails = [f"task {res.status}: {res.detail[:800]}"]
            rows.append(
                TaskRow(
                    task["id"],
                    task["family"],
                    not fails,
                    fails,
                    usage.tokens,
                    usage.cost,
                    int((time.monotonic() - start) * 1000),
                    reused=(routed_outcome(db, task_id) == "hit"),
                    detail=res.detail[:800],
                    digest=output_digest(res.result, res.files) if res.status == "done" else "",
                )
            )
            _report_row(rows[-1], task_id, db)
        except Exception as e:
            rows.append(
                TaskRow(
                    task["id"],
                    task["family"],
                    False,
                    [f"harness: {e}"][:1],
                    usage.tokens,
                    usage.cost,
                    int((time.monotonic() - start) * 1000),
                )
            )
            _report_row(rows[-1], task_id, db)
    return rows


def summarize(rows: list[TaskRow]) -> dict[str, Any]:
    n = len(rows)
    passed = sum(1 for r in rows if r.passed)
    return {
        "n": n,
        "passed": passed,
        "pass_rate": (passed / n) if n else 0.0,
        "cost_per_task": (sum(r.cost_usd for r in rows) / n) if n else 0.0,
        "tokens_per_task": (sum(r.tokens for r in rows) / n) if n else 0.0,
        "reuse_rate": (sum(1 for r in rows if r.reused) / n) if n else 0.0,
        "latency_ms_avg": (sum(r.latency_ms for r in rows) / n) if n else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    from medha.config import get_settings
    from medha.llm.client import LLMClient
    from medha.sandbox.runner import ModalSandboxRunner
    from medha.store.db import Database

    parser = argparse.ArgumentParser(description="MedhaBench runner")
    parser.add_argument(
        "--config", default="all", choices=["all", "a", "b", "c", "d", "compose", "repair"]
    )
    parser.add_argument("--tasks", default="bench/tasks/mini.yaml")
    parser.add_argument("--fixtures", default="bench/fixtures")
    parser.add_argument("--results", default="bench/results")
    args = parser.parse_args(argv)

    suite = load_all(args.tasks)
    tasks, repairs = suite["tasks"], suite["repair"]
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.results) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "ts": ts,
        "suite": args.tasks,
        "author": get_settings().LLM_AUTHOR_PROVIDER,
        "configs": {},
    }

    def fresh_env(name: str):
        s = get_settings()
        s.MEDHA_DB_PATH = str(out_dir / f"{name}.db")
        s.MEDHA_SKILLS_DIR = str(out_dir / f"{name}_skills")
        s.MEDHA_VERIFY_DIR = str(out_dir / f"{name}_verify")
        db = Database(s.MEDHA_DB_PATH)
        return s, db, LLMClient(s), ModalSandboxRunner()

    if args.config in ("all", "a"):
        s, db, llm, runner = fresh_env("a")
        rows = run_config_a(tasks, args.fixtures, llm, runner)
        report["configs"]["a"] = {"summary": summarize(rows), "rows": [asdict(r) for r in rows]}
    if args.config in ("all", "b"):
        s, db, llm, runner = fresh_env("b")
        rows = run_config_b(tasks, args.fixtures, db, llm, runner, s)
        report["configs"]["b"] = {"summary": summarize(rows), "rows": [asdict(r) for r in rows]}
    if args.config in ("all", "c", "d", "compose", "repair"):
        s, db, llm, runner = fresh_env("c")
        warmed = False
        if args.config in ("all", "c"):
            rows = run_config_c(tasks, args.fixtures, db, llm, runner, s)
            report["configs"]["c"] = {"summary": summarize(rows), "rows": [asdict(r) for r in rows]}
            warmed = True
        if args.config in ("all", "d"):
            if not warmed:
                run_config_c(tasks, args.fixtures, db, llm, runner, s)
                warmed = True
            d_rows = run_config_c(
                suite["heldout"], args.fixtures, db, llm, runner, s, prefix="bench-d"
            )
            report["configs"]["d"] = {
                "summary": summarize(d_rows),
                "rows": [asdict(r) for r in d_rows],
            }
        if args.config in ("all", "compose"):
            if not warmed:
                run_config_c(tasks, args.fixtures, db, llm, runner, s)
                warmed = True
            x_rows = run_config_c(
                suite["compose"], args.fixtures, db, llm, runner, s, prefix="bench-x"
            )
            report["configs"]["compose"] = {
                "summary": summarize(x_rows),
                "rows": [asdict(r) for r in x_rows],
            }
        if args.config in ("all", "repair"):
            if not warmed:
                run_config_c(tasks, args.fixtures, db, llm, runner, s)
                warmed = True
            rep_rows = run_config_c(repairs, args.fixtures, db, llm, runner, s, prefix="bench-r")
            report["configs"]["repair"] = {
                "summary": summarize(rep_rows),
                "rows": [asdict(r) for r in rep_rows],
            }

    (out_dir / "results.json").write_text(json.dumps(report, indent=2))
    # also drop a copy at the well-known latest path for /api/bench
    Path(args.results).mkdir(parents=True, exist_ok=True)
    (Path(args.results) / "latest.json").write_text(json.dumps(report, indent=2))

    from bench.report import print_table, write_charts

    print_table(report)
    for png in write_charts(report, out_dir):
        print(f"chart: {png}")
    print(f"results: {out_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
