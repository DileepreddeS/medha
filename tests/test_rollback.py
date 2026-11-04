"""Auto-rollback: v2 underperforming v1 within 3 runs restores v1."""

from medha.core import library, repair, reward
from medha.store.db import Database
from medha.store.models import SkillContract


def _setup(settings):
    db = Database(settings.MEDHA_DB_PATH)
    for v in (1, 2):
        c = SkillContract(
            skill_id="s", version=v, intent="t", postconditions_deterministic=["n == 1"]
        )
        library.stage_package(
            settings.MEDHA_SKILLS_DIR, "s", v, "def run(i,w):\n return {}", "# s", c, {}
        )
    library.activate(db, settings.MEDHA_SKILLS_DIR, "s", 1, "verified")
    for _ in range(8):
        reward.update(db, skill_id="s", version=1, reward=1.0)
    library.activate(db, settings.MEDHA_SKILLS_DIR, "s", 2, "verified")
    return db


def test_rollback_on_underperformance(settings):
    db = _setup(settings)
    reward.update(db, skill_id="s", version=2, reward=0.0)
    assert repair.maybe_auto_rollback(db, "s", 2) is False  # need 2 runs
    reward.update(db, skill_id="s", version=2, reward=0.0)
    assert repair.maybe_auto_rollback(db, "s", 2) is True
    assert library.active_version(db, "s") == 1


def test_no_rollback_when_good(settings):
    db = _setup(settings)
    reward.update(db, skill_id="s", version=2, reward=1.0)
    reward.update(db, skill_id="s", version=2, reward=1.0)
    assert repair.maybe_auto_rollback(db, "s", 2) is False
    assert library.active_version(db, "s") == 2


def test_no_rollback_for_v1(settings):
    db = _setup(settings)
    assert repair.maybe_auto_rollback(db, "s", 1) is False
