"""Final-report helpers: means, splits, and chart smoke."""

from bench.final_report import (
    chart_grouped,
    chart_pass_rates,
    chart_rate_bars,
    family_first_repeat,
    identical_rate,
    mean,
    pass_rate,
)

ROWS = [
    {
        "id": "s_v1",
        "family": "sales",
        "passed": True,
        "tokens": 100,
        "latency_ms": 1000,
        "digest": "aa",
        "reused": False,
    },
    {
        "id": "s_v2",
        "family": "sales",
        "passed": True,
        "tokens": 10,
        "latency_ms": 100,
        "digest": "aa",
        "reused": True,
    },
    {
        "id": "t_v1",
        "family": "top",
        "passed": False,
        "tokens": 200,
        "latency_ms": 2000,
        "digest": "bb",
        "reused": False,
    },
    {
        "id": "t_v2",
        "family": "top",
        "passed": True,
        "tokens": 20,
        "latency_ms": 200,
        "digest": "cc",
        "reused": True,
    },
]


def test_means_and_splits():
    assert pass_rate(ROWS) == 0.75
    firsts, repeats = family_first_repeat(ROWS)
    assert [r["id"] for r in firsts] == ["s_v1", "t_v1"]
    assert mean(firsts, "tokens") == 150
    assert mean(repeats, "tokens") == 15


def test_identical_rate():
    assert identical_rate(ROWS[:2]) == 1.0
    assert identical_rate(ROWS[2:]) == 0.5


def test_charts_smoke(tmp_path):
    p1 = chart_pass_rates(tmp_path, {"A": 0.9, "C": 0.8})
    p2 = chart_grouped(tmp_path, "g.png", "t", "tok", {"x": (1, 2)})
    p3 = chart_rate_bars(tmp_path, "r.png", "t", {"A": 0.2})
    assert p1.exists() and p2.exists() and p3.exists()
