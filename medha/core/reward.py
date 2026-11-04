"""Beta-posterior reward, cost/latency EMAs, trust levels, repair triggers."""

from __future__ import annotations

from typing import Any

from medha.store.db import Database
from medha.store.events import log_event, utcnow

EMA_K = 0.3


def expected_p(alpha: float, beta: float) -> float:
    return (alpha + 1) / (alpha + beta + 2)


def _outcome_of(reward: float) -> str:
    if reward >= 1.0:
        return "pass"
    if reward >= 0.5:
        return "corrected"
    return "fail"


def update(
    db: Database,
    *,
    skill_id: str,
    version: int,
    reward: float,
    cost: float = 0.0,
    latency_ms: int = 0,
    task_id: str = "",
) -> dict[str, Any]:
    row = db.query_one(
        "SELECT alpha, beta, cost_ema, latency_ema FROM versions WHERE skill_id=? AND version=?",
        (skill_id, version),
    )
    if row is None:
        raise KeyError(f"{skill_id} v{version} not registered")
    alpha = float(row["alpha"]) + reward
    beta = float(row["beta"]) + (1.0 - reward)
    cost_ema = float(row["cost_ema"]) + EMA_K * (cost - float(row["cost_ema"]))
    lat_ema = float(row["latency_ema"]) + EMA_K * (latency_ms - float(row["latency_ema"]))
    db.execute(
        "UPDATE versions SET alpha=?, beta=?, cost_ema=?, latency_ema=?"
        " WHERE skill_id=? AND version=?",
        (alpha, beta, cost_ema, lat_ema, skill_id, version),
    )
    run_id = db.execute(
        "INSERT INTO runs(task_id, skill_id, version, outcome, reward, cost_usd, latency_ms, ts)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (task_id, skill_id, version, _outcome_of(reward), reward, cost, latency_ms, utcnow()),
    )
    trust = _recompute_trust(db, skill_id)
    log_event(
        db,
        type="reward.updated",
        actor="reward",
        task_id=task_id,
        skill_id=skill_id,
        version=version,
        payload={
            "reward": reward,
            "run_id": run_id,
            "alpha": alpha,
            "beta": beta,
            "expected_p": expected_p(alpha, beta),
            "trust": trust,
        },
    )
    return {"run_id": run_id, "alpha": alpha, "beta": beta, "trust": trust}


def adjust(db: Database, *, run_id: int, new_reward: float, task_id: str = "") -> None:
    """Feedback adjustment: reverse the old reward, apply the new one."""
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise KeyError(f"run {run_id} not found")
    old = float(run["reward"])
    db.execute(
        "UPDATE runs SET reward=?, outcome=? WHERE id=?",
        (new_reward, _outcome_of(new_reward), run_id),
    )
    row = db.query_one(
        "SELECT alpha, beta FROM versions WHERE skill_id=? AND version=?",
        (run["skill_id"], run["version"]),
    )
    assert row is not None
    db.execute(
        "UPDATE versions SET alpha=?, beta=? WHERE skill_id=? AND version=?",
        (
            float(row["alpha"]) - old + new_reward,
            float(row["beta"]) - (1.0 - old) + (1.0 - new_reward),
            run["skill_id"],
            run["version"],
        ),
    )
    _recompute_trust(db, run["skill_id"])
    log_event(
        db,
        type="reward.adjusted",
        actor="reward",
        task_id=task_id,
        skill_id=run["skill_id"],
        version=run["version"],
        payload={"run_id": run_id, "old": old, "new": new_reward},
    )


def _recompute_trust(db: Database, skill_id: str) -> str:
    row = db.query_one("SELECT active_version, trust FROM skills WHERE id=?", (skill_id,))
    assert row is not None
    runs = db.query(
        "SELECT reward FROM runs WHERE skill_id=? AND version=? ORDER BY id DESC LIMIT 5",
        (skill_id, row["active_version"]),
    )
    total = db.query_one(
        "SELECT COUNT(*) c FROM runs WHERE skill_id=? AND version=?",
        (skill_id, row["active_version"]),
    )
    assert total is not None
    v = db.query_one(
        "SELECT alpha, beta FROM versions WHERE skill_id=? AND version=?",
        (skill_id, row["active_version"]),
    )
    assert v is not None
    trust = "verified"
    if (
        total["c"] >= 5
        and expected_p(float(v["alpha"]), float(v["beta"])) >= 0.9
        and len(runs) == 5
        and all(float(r["reward"]) >= 1.0 for r in runs)
    ):
        trust = "trusted"
    if trust != row["trust"]:
        db.execute("UPDATE skills SET trust=? WHERE id=?", (trust, skill_id))
    return trust


def should_repair(db: Database, skill_id: str, version: int) -> tuple[bool, str]:
    runs = db.query(
        "SELECT reward FROM runs WHERE skill_id=? AND version=? ORDER BY id DESC LIMIT 5",
        (skill_id, version),
    )
    if len(runs) >= 2 and float(runs[0]["reward"]) == 0.0 and float(runs[1]["reward"]) == 0.0:
        return True, "two consecutive failures"
    v = db.query_one(
        "SELECT alpha, beta FROM versions WHERE skill_id=? AND version=?", (skill_id, version)
    )
    total = db.query_one(
        "SELECT COUNT(*) c FROM runs WHERE skill_id=? AND version=?", (skill_id, version)
    )
    if v is not None and total is not None and total["c"] >= 3:
        ep = expected_p(float(v["alpha"]), float(v["beta"]))
        if ep < 0.7:
            return True, f"E[p]={ep:.2f} < 0.7 over {total['c']} runs"
    return False, ""


def run_count(db: Database, skill_id: str, version: int) -> int:
    row = db.query_one(
        "SELECT COUNT(*) c FROM runs WHERE skill_id=? AND version=?", (skill_id, version)
    )
    return int(row["c"]) if row else 0
