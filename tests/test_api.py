"""API: auth, learn→approve→done, SSE, skills, lessons, stats."""

import json

from fastapi.testclient import TestClient

from medha.api.app import create_app
from medha.llm.client import LLMClient
from medha.sandbox.runner import FakeSandboxRunner, SkillRunOutput, TestRunOutput
from medha.store.db import Database
from tests.conftest import StubBackend

SKILL = "def run(inputs, workdir):\n return {'n': 1}\n"
PLAN = json.dumps(
    {
        "goal": "Count rows",
        "inputs": {"data": {"type": "file", "formats": ["csv"]}},
        "outputs": {},
        "success_criteria": ["n == 1"],
        "sub_goals": [],
        "files": ["in.csv"],
    }
)
AUTHOR = json.dumps(
    {
        "skill_py": SKILL,
        "skill_md": "# S\n\nFine.\n",
        "contract": {
            "skill_id": "x",
            "version": 1,
            "intent": "count",
            "inputs": {"data": {"type": "file", "formats": ["csv"]}},
            "postconditions_deterministic": ["n == 1"],
        },
    }
)
TESTS = json.dumps({"tests_py": "def test_a(): pass\n"})


def _client(settings, runner):
    settings.MEDHA_TOKEN = "t"
    glimmer = StubBackend([(PLAN, 5, 5), (AUTHOR, 5, 5), (TESTS, 5, 5), (TESTS, 5, 5)])
    db = Database(settings.MEDHA_DB_PATH)
    app = create_app(
        settings,
        db,
        runner,
        make_llm=lambda: LLMClient(settings, glimmer_backend=glimmer, meta_backend=StubBackend([])),
    )
    return TestClient(app), db


def test_health_auth_and_flow(settings):
    runner = FakeSandboxRunner()
    runner.test_outputs = [TestRunOutput(passed=["t"])] * 2
    runner.skill_outputs = [SkillRunOutput(ok=True, result={"n": 1})] * 2
    client, _db = _client(settings, runner)

    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/skills").status_code == 401
    h = {"Authorization": "Bearer t"}

    r = client.post(
        "/api/tasks",
        data={"text": "count rows"},
        files=[("files", ("in.csv", b"a,b\n1,2\n", "text/csv"))],
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_approval"
    tid, aid = body["task_id"], body["approval_id"]

    sse = client.get(f"/api/tasks/{tid}/events", headers=h)
    assert "approval.requested" in sse.text and "task.routed" in sse.text

    d = client.post(f"/api/approvals/{aid}", json={"decision": "approve"}, headers=h).json()
    assert d["status"] == "done" and d["result"] == {"n": 1}

    fb = client.post(f"/api/tasks/{tid}/feedback", json={"kind": "accept"}, headers=h).json()
    assert fb == {"ok": True}

    skills = client.get("/api/skills", headers=h).json()
    assert len(skills) == 1 and skills[0]["id"] == "count_rows"
    detail = client.get("/api/skills/count_rows", headers=h).json()
    assert detail["contract"]["intent"] == "count"
    assert "def run(inputs, workdir)" in detail["skill_py"]
    assert detail["prev_skill_py"] is None and detail["prev_version"] is None
    rb = client.post("/api/skills/count_rows/rollback", json={"to_version": 1}, headers=h).json()
    assert rb == {"ok": True, "active_version": 1}
    assert client.get("/api/lessons", headers=h).json() == []
    assert client.delete("/api/lessons/99", headers=h).json() == {"deleted": False}
    stats = client.get("/api/stats", headers=h).json()
    assert stats["tasks_total"] == 1 and stats["skills"] == 1
    assert client.get("/api/bench", headers=h).json() == {"runs": []}
    act = client.get("/api/activity", headers=h).json()
    assert act["points"] and act["points"][-1]["skills"] == 1
    assert act["points"][-1]["runs"] >= 1
    assert "result.json" in client.get(f"/api/tasks/{tid}/outputs", headers=h).json()
    assert client.get("/api/skills/nope", headers=h).status_code == 404
    assert (
        client.post(
            "/api/tasks", data={"text": "x"}, files=[("files", ("evil.exe", b"1", "x"))], headers=h
        ).status_code
        == 400
    )


def test_bench_lists_nested_runs(settings):
    """Bench results live in per-run dirs; the endpoint serves them all."""
    from pathlib import Path

    runner = FakeSandboxRunner()
    client, _db = _client(settings, runner)
    h = {"Authorization": "Bearer " + "t"}
    run = Path(settings.MEDHA_BENCH_RESULTS) / "20261104T000000Z"
    run.mkdir(parents=True)
    (run / "results.json").write_text(json.dumps({"ts": "t", "suite": "s", "configs": {}}))
    body = client.get("/api/bench", headers=h).json()
    assert [r["ts"] for r in body["runs"]] == ["t"]


def test_demo_presets_and_files(settings):
    runner = FakeSandboxRunner()
    client, _db = _client(settings, runner)
    h = {"Authorization": "Bearer " + "t"}
    presets = client.get("/api/demo/presets", headers=h).json()
    assert [p["id"] for p in presets] == ["learn", "reuse", "stock", "repair", "compose"]
    assert all(p["text"] and p["files"] for p in presets)
    csv = client.get("/api/demo/files/sales_v1.csv", headers=h)
    assert csv.status_code == 200 and b"date" in csv.content
    assert client.get("/api/demo/files/../../pyproject.toml", headers=h).status_code in (404, 422)
    assert client.get("/api/demo/files/nope.csv", headers=h).status_code == 404
