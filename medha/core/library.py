"""Skill package IO + registry. Version dirs are immutable once staged."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medha.store.db import Database
from medha.store.events import log_event, utcnow
from medha.store.models import SkillContract


class PackageExists(Exception):
    pass


class UnknownVersion(Exception):
    pass


@dataclass
class SkillVersionView:
    skill_id: str
    version: int
    path: str
    contract: SkillContract
    skill_py: str
    skill_md: str
    provenance: dict[str, Any]


def package_dir(skills_dir: str | Path, skill_id: str, version: int) -> Path:
    return Path(skills_dir) / skill_id / f"v{version}"


def stage_package(
    skills_dir: str | Path,
    skill_id: str,
    version: int,
    skill_py: str,
    skill_md: str,
    contract: SkillContract,
    provenance: dict[str, Any],
) -> Path:
    dest = package_dir(skills_dir, skill_id, version)
    if dest.exists():
        raise PackageExists(f"{skill_id} v{version} already staged (immutable)")
    (dest / "tests" / "visible").mkdir(parents=True)
    (dest / "skill.py").write_text(skill_py)
    (dest / "SKILL.md").write_text(skill_md)
    (dest / "contract.json").write_text(contract.model_dump_json(indent=2))
    (dest / "provenance.json").write_text(json.dumps(provenance, indent=2))
    return dest


def read_package(skills_dir: str | Path, skill_id: str, version: int) -> SkillVersionView:
    dest = package_dir(skills_dir, skill_id, version)
    return SkillVersionView(
        skill_id=skill_id,
        version=version,
        path=str(dest),
        contract=SkillContract.model_validate_json((dest / "contract.json").read_text()),
        skill_py=(dest / "skill.py").read_text(),
        skill_md=(dest / "SKILL.md").read_text(),
        provenance=json.loads((dest / "provenance.json").read_text()),
    )


def write_visible_tests(skills_dir: str | Path, skill_id: str, version: int, tests_py: str) -> Path:
    path = package_dir(skills_dir, skill_id, version) / "tests" / "visible" / "tests.py"
    path.write_text(tests_py)
    return path


def activate(
    db: Database,
    skills_dir: str | Path,
    skill_id: str,
    version: int,
    trust: str,
    task_id: str = "",
) -> None:
    path = str(package_dir(skills_dir, skill_id, version))
    if not db.query_one("SELECT id FROM skills WHERE id=?", (skill_id,)):
        db.execute(
            "INSERT INTO skills(id, active_version, trust, created_at) VALUES (?,?,?,?)",
            (skill_id, version, trust, utcnow()),
        )
    else:
        db.execute(
            "UPDATE skills SET active_version=?, trust=? WHERE id=?", (version, trust, skill_id)
        )
    db.execute(
        "INSERT INTO versions(skill_id, version, path, status) VALUES (?,?,?,?)"
        " ON CONFLICT(skill_id, version) DO UPDATE SET status=excluded.status",
        (skill_id, version, path, trust),
    )
    log_event(
        db,
        type="skill.registered",
        actor="library",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={"trust": trust, "path": path},
    )


def active_version(db: Database, skill_id: str) -> int | None:
    row = db.query_one("SELECT active_version FROM skills WHERE id=?", (skill_id,))
    return int(row["active_version"]) if row else None


def set_active_version(db: Database, skill_id: str, version: int, task_id: str = "") -> None:
    if not db.query_one(
        "SELECT status FROM versions WHERE skill_id=? AND version=?", (skill_id, version)
    ):
        raise UnknownVersion(f"{skill_id} v{version} is not registered")
    db.execute("UPDATE skills SET active_version=? WHERE id=?", (version, skill_id))
    log_event(
        db,
        type="skill.rollback",
        actor="library",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={},
    )


def list_skills(db: Database) -> list[dict[str, Any]]:
    return db.query(
        "SELECT s.id, s.active_version, s.trust, v.alpha, v.beta, v.cost_ema,"
        " v.latency_ema, v.status FROM skills s"
        " JOIN versions v ON v.skill_id=s.id AND v.version=s.active_version"
        " ORDER BY s.id"
    )
