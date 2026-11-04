"""Store roundtrip: tables + append-only events."""

from medha.store.db import Database
from medha.store.events import events_for_task, log_event, recent_events


def test_tables_and_events(settings):
    db = Database(settings.MEDHA_DB_PATH)
    eid = log_event(
        db,
        type="task.received",
        actor="test",
        task_id="t1",
        payload={"a": 1},
        tokens_in=3,
        tokens_out=4,
        cost_usd=0.5,
    )
    assert eid == 1
    log_event(db, type="task.done", actor="test", task_id="t1")
    rows = events_for_task(db, "t1")
    assert [r["type"] for r in rows] == ["task.received", "task.done"]
    assert rows[0]["tokens_in"] == 3 and rows[0]["cost_usd"] == 0.5
    assert len(recent_events(db)) == 2

    db.execute(
        "INSERT INTO skills(id, active_version, trust, created_at) VALUES (?,?,?,?)",
        ("s1", 1, "verified", "now"),
    )
    db.execute(
        "INSERT INTO versions(skill_id, version, path, status) VALUES (?,?,?,?)",
        ("s1", 1, "/p", "verified"),
    )
    v = db.query_one("SELECT alpha, beta FROM versions WHERE skill_id=?", ("s1",))
    assert (v["alpha"], v["beta"]) == (0, 0)
    db.execute(
        "INSERT INTO runs(task_id, skill_id, version, outcome, reward, ts) VALUES (?,?,?,?,?,?)",
        ("t1", "s1", 1, "pass", 1.0, "now"),
    )
    db.execute(
        "INSERT INTO cases(skill_id, input_name, input_blob, source) VALUES (?,?,?,?)",
        ("s1", "in.csv", b"a", "accepted"),
    )
    db.execute(
        "INSERT INTO lessons(scope, trigger_text, guidance, created_at) VALUES (?,?,?,?)",
        ("global", "t", "g", "now"),
    )
    db.execute(
        "INSERT INTO approvals(task_id, skill_id, version, ts) VALUES (?,?,?,?)",
        ("t1", "s1", 1, "now"),
    )
    assert db.query("SELECT * FROM runs")[0]["reward"] == 1.0
    assert db.query("SELECT * FROM cases")[0]["input_blob"] == b"a"
