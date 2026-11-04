"""Lessons: short, structured, event-cited, capped, scanned, deletable.

Built only from structured failure/correction context — never raw tool
text (memory-poisoning defense). Injected into author/repair prompts as data.
"""

from __future__ import annotations

from typing import Any

from medha.security.scan import scan_markdown
from medha.store.db import Database
from medha.store.events import log_event, utcnow

MAX_GUIDANCE = 300
MAX_TRIGGER = 200


class LessonRejected(Exception):
    pass


def create_lesson(
    db: Database,
    *,
    scope: str,
    trigger: str,
    guidance: str,
    evidence_ids: list[int],
    task_id: str = "",
) -> int:
    trigger = " ".join(trigger.split())[:MAX_TRIGGER]
    guidance = " ".join(guidance.split())[:MAX_GUIDANCE]
    if not trigger or not guidance:
        raise LessonRejected("trigger and guidance must be non-empty")
    findings = scan_markdown(trigger + "\n" + guidance)
    if findings:
        raise LessonRejected(f"injection patterns: {[f.rule for f in findings][:3]}")
    lid = db.execute(
        "INSERT INTO lessons(scope, trigger_text, guidance, evidence_event_ids_json, created_at)"
        " VALUES (?,?,?,?,?)",
        (scope, trigger, guidance, str(evidence_ids), utcnow()),
    )
    log_event(
        db,
        type="lesson.created",
        actor="lessons",
        task_id=task_id,
        payload={"lesson_id": lid, "scope": scope},
    )
    return lid


def list_lessons(db: Database, scope: str | None = None) -> list[dict[str, Any]]:
    if scope:
        return db.query("SELECT * FROM lessons WHERE scope=? ORDER BY id", (scope,))
    return db.query("SELECT * FROM lessons ORDER BY id")


def delete_lesson(db: Database, lesson_id: int, task_id: str = "") -> bool:
    if not db.query_one("SELECT id FROM lessons WHERE id=?", (lesson_id,)):
        return False
    db.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
    log_event(
        db,
        type="lesson.deleted",
        actor="lessons",
        task_id=task_id,
        payload={"lesson_id": lesson_id},
    )
    return True


def lessons_for_prompt(db: Database, skill_id: str) -> str:
    rows = db.query(
        "SELECT trigger_text, guidance FROM lessons WHERE scope IN ('global', ?) ORDER BY id",
        (skill_id,),
    )
    return "\n".join(f"- {r['trigger_text']} -> {r['guidance']}" for r in rows)
