"""Library: immutable packages, registry, rollback, run + events."""

import pytest

from medha.core import library
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput
from medha.store.db import Database
from medha.store.events import events_for_task
from medha.store.models import SkillContract

SKILL_PY = 'def run(inputs, workdir):\n return {"n": 1}\n'
SKILL_MD = "# S\n\nDoes things.\n"


def _contract():
    return SkillContract(
        skill_id="s1",
        version=1,
        intent="test",
        postconditions_deterministic=["x == 1"],
    )


def test_stage_read_immutable(settings):
    d = library.stage_package(
        settings.MEDHA_SKILLS_DIR, "s1", 1, SKILL_PY, SKILL_MD, _contract(), {"by": "test"}
    )
    assert (d / "skill.py").exists()
    view = library.read_package(settings.MEDHA_SKILLS_DIR, "s1", 1)
    assert view.contract.intent == "test" and view.skill_py == SKILL_PY
    with pytest.raises(library.PackageExists):
        library.stage_package(
            settings.MEDHA_SKILLS_DIR, "s1", 1, SKILL_PY, SKILL_MD, _contract(), {}
        )


def test_activate_run_rollback(settings):
    db = Database(settings.MEDHA_DB_PATH)
    for v in (1, 2):
        c = _contract()
        c.version = v
        library.stage_package(settings.MEDHA_SKILLS_DIR, "s1", v, SKILL_PY, SKILL_MD, c, {})
        library.activate(db, settings.MEDHA_SKILLS_DIR, "s1", v, "verified", task_id="t1")
    assert library.active_version(db, "s1") == 2
    library.set_active_version(db, "s1", 1, task_id="t1")
    assert library.active_version(db, "s1") == 1
    with pytest.raises(library.UnknownVersion):
        library.set_active_version(db, "s1", 9)
    assert len(library.list_skills(db)) == 1

    runner = FakeSandboxRunner()
    runner.skill_outputs.append(SkillRunOutput(ok=True, result={"n": 1}))
    view = library.read_package(settings.MEDHA_SKILLS_DIR, "s1", 1)
    out = runner.run_skill(view.skill_py, {"in.csv": b"a"})
    assert out.ok and out.result == {"n": 1}
    types = [e["type"] for e in events_for_task(db, "t1")]
    assert types.count("skill.registered") == 2 and "skill.rollback" in types
