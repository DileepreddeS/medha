"""Reward: Beta posterior, EMAs, trust promotion/demotion, repair triggers."""

from medha.core import library, reward
from medha.store.db import Database
from medha.store.models import SkillContract


def _db(settings):
    db = Database(settings.MEDHA_DB_PATH)
    c = SkillContract(skill_id="s", version=1, intent="t", postconditions_deterministic=["n == 1"])
    library.stage_package(
        settings.MEDHA_SKILLS_DIR, "s", 1, "def run(i,w):\n return {}", "# s", c, {}
    )
    library.activate(db, settings.MEDHA_SKILLS_DIR, "s", 1, "verified")
    return db


def test_expected_p_math():
    assert reward.expected_p(0, 0) == 0.5
    assert reward.expected_p(4, 0) == 5 / 6


def test_update_stats_and_ema(settings):
    db = _db(settings)
    out = reward.update(db, skill_id="s", version=1, reward=1.0, cost=0.5, latency_ms=100)
    row = db.query_one("SELECT alpha, beta, cost_ema, latency_ema FROM versions WHERE skill_id='s'")
    assert (row["alpha"], row["beta"]) == (1.0, 0.0)
    assert row["cost_ema"] == 0.15 and row["latency_ema"] == 30.0
    assert out["trust"] == "verified"
    row = db.query_one("SELECT outcome FROM runs WHERE id=?", (out["run_id"],))
    assert row is not None and row["outcome"] == "pass"


def test_trust_promotion_and_demotion(settings):
    db = _db(settings)
    # E[p]=(a+1)/(a+b+2) needs 8 perfect runs to reach 0.9
    for _ in range(8):
        out = reward.update(db, skill_id="s", version=1, reward=1.0)
    assert out["trust"] == "trusted"
    out = reward.update(db, skill_id="s", version=1, reward=0.0)
    assert out["trust"] == "verified"


def test_adjust_for_feedback(settings):
    db = _db(settings)
    out = reward.update(db, skill_id="s", version=1, reward=1.0)
    reward.adjust(db, run_id=out["run_id"], new_reward=0.5)
    row = db.query_one("SELECT alpha, beta FROM versions WHERE skill_id='s'")
    assert (row["alpha"], row["beta"]) == (0.5, 0.5)


def test_should_repair_triggers(settings):
    db = _db(settings)
    assert reward.should_repair(db, "s", 1) == (False, "")
    reward.update(db, skill_id="s", version=1, reward=0.0)
    reward.update(db, skill_id="s", version=1, reward=0.0)
    flag, reason = reward.should_repair(db, "s", 1)
    assert flag and "consecutive" in reason


def test_should_repair_low_posterior(settings):
    db = _db(settings)
    reward.update(db, skill_id="s", version=1, reward=0.0)
    reward.update(db, skill_id="s", version=1, reward=1.0)
    reward.update(db, skill_id="s", version=1, reward=0.0)
    flag, reason = reward.should_repair(db, "s", 1)
    assert flag and "E[p]" in reason
