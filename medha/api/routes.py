"""HTTP routes (spec §5). Mutations commit the Modal Volume at the boundary."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from medha.api.auth import make_auth, make_rate_limit
from medha.api.models import (
    ApprovalRequest,
    EventOut,
    FeedbackRequest,
    RollbackRequest,
    SkillDetail,
    SkillSummary,
    StatsResponse,
    TaskResponse,
)
from medha.core import lessons, library, orchestrator, reward
from medha.llm.client import Budget, LLMClient
from medha.store.events import events_for_task

ALLOWED_EXTS = {".csv", ".xlsx", ".pdf", ".txt", ".json", ".md"}
MAX_FILE_BYTES = 10 * 1024 * 1024
TERMINAL_EVENTS = {"task.done", "task.failed", "approval.requested"}

# One-click demo presets: fixed text + fixture bytes, so DEMO_MODE cache
# keys are stable between record and replay. Whitelist only, no traversal.
DEMO_FILES = ("sales_v1.csv", "sales_v2.csv", "stock_v1.csv", "sales_r4.csv")
DEMO_PRESETS = (
    {
        "id": "learn",
        "label": "1 · Learn a skill",
        "text": "Summarize this POS sales CSV. Return totals by category, the grand"
        " total, and a Markdown report that lists every category name.",
        "files": ["sales_v1.csv"],
    },
    {
        "id": "reuse",
        "label": "2 · Reuse it",
        "text": "Summarize this POS sales CSV. Return totals by category, the grand"
        " total, and a Markdown report that lists every category name.",
        "files": ["sales_v2.csv"],
    },
    {
        "id": "stock",
        "label": "3 · Learn stock alerts",
        "text": "List items that need reordering: rows where qty <= reorder_at.",
        "files": ["stock_v1.csv"],
    },
    {
        "id": "repair",
        "label": "4 · Survive drift",
        "text": "Summarize this POS sales CSV. Return totals by category, the grand"
        " total, and a Markdown report that lists every category name.",
        "files": ["sales_r4.csv"],
    },
    {
        "id": "compose",
        "label": "5 · Compose a pack",
        "text": "Build the weekly ops pack: first summarize POS sales by category,"
        " then list items that need reordering.",
        "files": ["sales_v1.csv", "stock_v1.csv"],
    },
)


@dataclass
class Context:
    settings: Any
    db: Any
    runner: Any
    make_llm: Callable[[], LLMClient]
    commit: Callable[[], None] | None = None


def _commit(ctx: Context) -> None:
    if ctx.commit is not None:
        ctx.commit()


def _task_response(res: Any) -> TaskResponse:
    return TaskResponse(
        task_id=res.task_id,
        status=res.status,
        skill_id=res.skill_id,
        version=res.version,
        result=res.result,
        file_names=sorted(res.files),
        detail=res.detail,
        approval_id=res.approval_id,
    )


def _event_out(row: dict[str, Any]) -> EventOut:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return EventOut(
        id=row["id"],
        ts=row["ts"],
        type=row["type"],
        actor=row["actor"],
        skill_id=row.get("skill_id"),
        version=row.get("version"),
        payload=payload,
        tokens_in=row.get("tokens_in", 0),
        tokens_out=row.get("tokens_out", 0),
        cost_usd=row.get("cost_usd", 0.0),
        latency_ms=row.get("latency_ms", 0),
    )


def build_routers(ctx: Context) -> tuple[APIRouter, APIRouter]:
    public = APIRouter()
    protected = APIRouter(
        dependencies=[Depends(make_auth(ctx.settings)), Depends(make_rate_limit())]
    )

    @public.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @protected.post("/api/tasks")
    async def create_task(
        text: str = Form(default=""), files: list[UploadFile] | None = None
    ) -> TaskResponse:
        blobs: dict[str, bytes] = {}
        for f in (files or [])[:5]:
            data = await f.read()
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(status_code=400, detail=f"{f.filename}: over 10 MB")
            if Path(f.filename or "").suffix.lower() not in ALLOWED_EXTS:
                raise HTTPException(status_code=400, detail=f"{f.filename}: bad type")
            blobs[f.filename or "upload"] = data
        task_id = uuid.uuid4().hex[:12]
        res = await asyncio.to_thread(
            orchestrator.run_task,
            db=ctx.db,
            llm=ctx.make_llm(),
            runner=ctx.runner,
            settings=ctx.settings,
            task_id=task_id,
            task_text=text,
            files=blobs,
        )
        _commit(ctx)
        return _task_response(res)

    @protected.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: str, last_id: int = 0) -> StreamingResponse:
        async def gen():
            seen, idle = last_id, 0
            while True:
                rows = ctx.db.query(
                    "SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id",
                    (task_id, seen),
                )
                for r in rows:
                    seen = r["id"]
                    yield f"data: {_event_out(r).model_dump_json()}\n\n"
                    if r["type"] in TERMINAL_EVENTS:
                        return
                await asyncio.sleep(0.4)
                idle += 1
                if idle % 37 == 0:
                    yield ": ping\n\n"
                if idle > 750:
                    return

        return StreamingResponse(gen(), media_type="text/event-stream")

    @protected.post("/api/tasks/{task_id}/feedback")
    async def feedback(task_id: str, body: FeedbackRequest) -> dict[str, Any]:
        out = await asyncio.to_thread(
            orchestrator.submit_feedback,
            db=ctx.db,
            llm=ctx.make_llm(),
            runner=ctx.runner,
            settings=ctx.settings,
            task_id=task_id,
            kind=body.kind,
            text=body.text,
        )
        _commit(ctx)
        return out

    @protected.post("/api/approvals/{approval_id}")
    async def decide_approval(approval_id: int, body: ApprovalRequest) -> TaskResponse:
        res = await asyncio.to_thread(
            orchestrator.decide_approval,
            db=ctx.db,
            llm=ctx.make_llm(),
            runner=ctx.runner,
            settings=ctx.settings,
            approval_id=approval_id,
            decision=body.decision,
        )
        _commit(ctx)
        return _task_response(res)

    @protected.get("/api/skills")
    def list_skills() -> list[SkillSummary]:
        out = []
        for row in library.list_skills(ctx.db):
            vers = ctx.db.query(
                "SELECT version FROM versions WHERE skill_id=? ORDER BY version", (row["id"],)
            )
            out.append(
                SkillSummary(
                    id=row["id"],
                    active_version=row["active_version"],
                    trust=row["trust"],
                    expected_p=reward.expected_p(float(row["alpha"]), float(row["beta"])),
                    runs=reward.run_count(ctx.db, row["id"], row["active_version"]),
                    cost_per_run=float(row["cost_ema"]),
                    versions=[v["version"] for v in vers],
                )
            )
        return out

    @protected.get("/api/skills/{skill_id}")
    def skill_detail(skill_id: str) -> SkillDetail:
        ver = library.active_version(ctx.db, skill_id)
        if ver is None:
            raise HTTPException(status_code=404, detail="unknown skill")
        view = library.read_package(ctx.settings.MEDHA_SKILLS_DIR, skill_id, ver)
        prev_py: str | None = None
        prev_ver: int | None = None
        if ver > 1:
            try:
                prev = library.read_package(ctx.settings.MEDHA_SKILLS_DIR, skill_id, ver - 1)
            except (FileNotFoundError, NotADirectoryError, OSError):
                prev = None
            if prev is not None:
                prev_py, prev_ver = prev.skill_py, ver - 1
        versions = ctx.db.query(
            "SELECT version, alpha, beta, cost_ema, latency_ema, status FROM versions"
            " WHERE skill_id=? ORDER BY version",
            (skill_id,),
        )
        runs = ctx.db.query(
            "SELECT id, task_id, version, outcome, reward, ts FROM runs WHERE skill_id=?"
            " ORDER BY id DESC LIMIT 10",
            (skill_id,),
        )
        return SkillDetail(
            id=skill_id,
            contract=json.loads(view.contract.model_dump_json()),
            skill_md=view.skill_md,
            skill_py=view.skill_py,
            prev_skill_py=prev_py,
            prev_version=prev_ver,
            versions=versions,
            lineage=list(view.provenance.get("composed_of", [])),
            recent_runs=runs,
        )

    @protected.post("/api/skills/{skill_id}/rollback")
    def rollback(skill_id: str, body: RollbackRequest) -> dict[str, Any]:
        try:
            library.set_active_version(ctx.db, skill_id, body.to_version)
        except library.UnknownVersion as e:
            raise HTTPException(status_code=404, detail="unknown skill/version") from e
        _commit(ctx)
        return {"ok": True, "active_version": body.to_version}

    @protected.get("/api/lessons")
    def get_lessons() -> list[dict[str, Any]]:
        return lessons.list_lessons(ctx.db)

    @protected.delete("/api/lessons/{lesson_id}")
    def delete_lesson(lesson_id: int) -> dict[str, bool]:
        deleted = lessons.delete_lesson(ctx.db, lesson_id)
        _commit(ctx)
        return {"deleted": deleted}

    @protected.get("/api/stats")
    def stats() -> StatsResponse:
        s = ctx.settings
        spend = Budget(s.DAILY_SPEND_CAP_USD, s.COST_LOG_PATH).spent_today()
        tok = ctx.db.query_one("SELECT COALESCE(SUM(tokens_in+tokens_out),0) t FROM events")
        tasks = ctx.db.query_one(
            "SELECT COUNT(DISTINCT task_id) c FROM events WHERE type='task.received'"
        )
        routed = ctx.db.query("SELECT payload_json FROM events WHERE type='task.routed'")
        hits = 0
        for r in routed:
            try:
                if json.loads(r["payload_json"]).get("outcome") == "hit":
                    hits += 1
            except json.JSONDecodeError:
                pass
        n_skills = ctx.db.query_one("SELECT COUNT(*) c FROM skills")
        n_ver = ctx.db.query_one("SELECT COUNT(*) c FROM versions")
        return StatsResponse(
            spend_today_usd=spend,
            spend_cap_usd=s.DAILY_SPEND_CAP_USD,
            tokens_total=int(tok["t"]) if tok else 0,
            tasks_total=int(tasks["c"]) if tasks else 0,
            reuse_rate=(hits / len(routed)) if routed else 0.0,
            skills=int(n_skills["c"]) if n_skills else 0,
            versions=int(n_ver["c"]) if n_ver else 0,
        )

    @protected.get("/api/bench")
    def bench() -> dict[str, Any]:
        base = Path(ctx.settings.MEDHA_BENCH_RESULTS)
        paths = sorted(base.glob("*/results.json"), key=lambda p: p.stat().st_mtime)
        runs = []
        for p in paths:
            try:
                runs.append(json.loads(p.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return {"runs": runs}

    @protected.get("/api/activity")
    def activity() -> dict[str, Any]:
        rows = ctx.db.query(
            "SELECT ts, type, skill_id FROM events"
            " WHERE type IN ('skill.registered','task.done') ORDER BY id"
        )
        points: list[dict[str, Any]] = []
        skills: set[str] = set()
        n_runs = 0
        for r in rows[-2000:]:
            if r["type"] == "skill.registered" and r["skill_id"]:
                skills.add(r["skill_id"])
            elif r["type"] == "task.done":
                n_runs += 1
            points.append({"ts": r["ts"], "skills": len(skills), "runs": n_runs})
        return {"points": points[-500:]}

    @protected.get("/api/tasks/{task_id}/outputs")
    def list_outputs(task_id: str) -> list[str]:
        dest = orchestrator.task_dir(ctx.settings, task_id) / "outputs"
        if not dest.exists():
            return []
        return sorted(p.name for p in dest.iterdir() if p.is_file())

    @protected.get("/api/tasks/{task_id}/outputs/{name}")
    def get_output(task_id: str, name: str) -> Response:
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(status_code=400, detail="bad name")
        path = orchestrator.task_dir(ctx.settings, task_id) / "outputs" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="missing")
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    # used by tests/other views; harmless in prod
    @protected.get("/api/tasks/{task_id}/all-events")
    def all_events(task_id: str) -> list[EventOut]:
        return [_event_out(r) for r in events_for_task(ctx.db, task_id)]

    @protected.get("/api/demo/presets")
    def demo_presets() -> list[dict[str, Any]]:
        return [
            {"id": p["id"], "label": p["label"], "text": p["text"], "files": list(p["files"])}
            for p in DEMO_PRESETS
        ]

    @protected.get("/api/demo/files/{name}")
    def demo_file(name: str) -> Response:
        if name not in DEMO_FILES:
            raise HTTPException(status_code=404, detail="no such demo file")
        root = Path(__file__).resolve().parent.parent.parent
        path = root / "bench" / "fixtures" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="missing fixture")
        return Response(content=path.read_bytes(), media_type="text/csv")

    return public, protected
