"""Final cross-run report: README numbers + docs/charts PNGs.

Reads the definitive results.json files (RUNS manifest), writes combined
charts, and prints the markdown numbers for the README results section.
Re-run after the Glimmer mini lands (fill mini_c_glimmer first).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

RUNS: dict[str, str | None] = {
    "mini_a": "bench/results/20261103T213232Z/results.json",
    "mini_b": "bench/results/20261103T231138Z/results.json",
    "mini_c": "bench/results/20261104T003612Z/results.json",
    "mini_c_glimmer": "bench/results/20261104T040003Z/results.json",
    "econ_a": "bench/results/20261104T013508Z/results.json",
    "econ_c": "bench/results/20261104T034140Z/results.json",
    "con_a": "bench/results/20261104T011727Z/results.json",
    "con_c": "bench/results/20261104T011935Z/results.json",
    "repair_a": "bench/results/20261104T012613Z/results.json",
    "repair_c": "bench/results/20261103T194836Z/results.json",
}

CHART_DIR = Path("docs/charts")


def rows_of(path: str, config: str) -> list[dict[str, Any]]:
    with open(path) as f:
        report = json.load(f)
    return report["configs"][config]["rows"]  # type: ignore[no-any-return]


def pass_rate(rows: list[dict[str, Any]]) -> float:
    return sum(1 for r in rows if r["passed"]) / len(rows)


def family_first_repeat(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    fams: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        fams[r["family"]].append(r)
    firsts, repeats = [], []
    for ts in fams.values():
        firsts.append(ts[0])
        repeats.extend(ts[1:])
    return firsts, repeats


def mean(rows: list[dict[str, Any]], key: str) -> float:
    return sum(r[key] for r in rows) / len(rows)


def identical_rate(rows: list[dict[str, Any]]) -> float:
    base = rows[0].get("digest")
    return sum(1 for r in rows if r.get("digest") == base) / len(rows)


def _mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def chart_pass_rates(out: Path, rates: dict[str, float]) -> Path:
    plt = _mpl()
    names = list(rates)
    fig, ax = plt.subplots()
    ax.bar(names, [rates[n] for n in names])
    ax.set_ylim(0, 1)
    ax.set_title("Mini MedhaBench pass rate (12 tasks)")
    ax.set_ylabel("pass rate")
    for i, n in enumerate(names):
        ax.text(i, rates[n] + 0.02, f"{rates[n]:.0%}", ha="center")
    p = out / "pass_rate_abc.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def chart_grouped(
    out: Path, name: str, title: str, ylabel: str, groups: dict[str, tuple[float, float]]
) -> Path:
    """groups: label -> (A value, C value)."""
    plt = _mpl()
    labels = list(groups)
    a = [groups[lbl][0] for lbl in labels]
    c = [groups[lbl][1] for lbl in labels]
    x = list(range(len(labels)))
    fig, ax = plt.subplots()
    ax.bar([v - 0.2 for v in x], a, 0.4, label="A single-shot")
    ax.bar([v + 0.2 for v in x], c, 0.4, label="C medha")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.legend()
    p = out / name
    fig.savefig(p)
    plt.close(fig)
    return p


def chart_rate_bars(out: Path, name: str, title: str, rates: dict[str, float]) -> Path:
    plt = _mpl()
    names = list(rates)
    fig, ax = plt.subplots()
    ax.bar(names, [rates[n] for n in names])
    ax.set_ylim(0, 1)
    ax.set_title(title)
    ax.set_ylabel("rate")
    for i, n in enumerate(names):
        ax.text(i, rates[n] + 0.02, f"{rates[n]:.0%}", ha="center")
    p = out / name
    fig.savefig(p)
    plt.close(fig)
    return p


def main() -> dict[str, Any]:
    assert RUNS["mini_c_glimmer"], "fill mini_c_glimmer first"
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    mini_rates = {
        "A": pass_rate(rows_of(RUNS["mini_a"], "a")),  # type: ignore[arg-type]
        "B": pass_rate(rows_of(RUNS["mini_b"], "b")),  # type: ignore[arg-type]
        "C (Spark)": pass_rate(rows_of(RUNS["mini_c"], "c")),  # type: ignore[arg-type]
        "C (Glimmer)": pass_rate(rows_of(RUNS["mini_c_glimmer"], "c")),  # type: ignore[arg-type]
    }
    ea, ec = rows_of(RUNS["econ_a"], "a"), rows_of(RUNS["econ_c"], "c")  # type: ignore[arg-type]
    ea_f, ea_r = family_first_repeat(ea)
    ec_f, ec_r = family_first_repeat(ec)
    con_a, con_c = rows_of(RUNS["con_a"], "a"), rows_of(RUNS["con_c"], "c")  # type: ignore[arg-type]
    ra, rc = rows_of(RUNS["repair_a"], "a"), rows_of(RUNS["repair_c"], "repair")  # type: ignore[arg-type]

    charts = [
        chart_pass_rates(CHART_DIR, mini_rates),
        chart_grouped(
            CHART_DIR,
            "repeat_tokens.png",
            "Tokens per task: first vs repeats (6 families)",
            "tokens",
            {
                "first": (mean(ea_f, "tokens"), mean(ec_f, "tokens")),
                "repeat": (mean(ea_r, "tokens"), mean(ec_r, "tokens")),
            },
        ),
        chart_grouped(
            CHART_DIR,
            "repeat_latency.png",
            "Latency per task: first vs repeats (6 families)",
            "ms",
            {
                "first": (mean(ea_f, "latency_ms"), mean(ec_f, "latency_ms")),
                "repeat": (mean(ea_r, "latency_ms"), mean(ec_r, "latency_ms")),
            },
        ),
        chart_rate_bars(
            CHART_DIR,
            "consistency.png",
            "Identical-output rate (same task 5x)",
            {"A": identical_rate(con_a), "C": identical_rate(con_c)},
        ),
        chart_grouped(
            CHART_DIR,
            "repair_cost.png",
            "Repair suite 3/3 both: cost per drift",
            "tokens",
            {"tokens": (mean(ra, "tokens"), mean(rc, "tokens"))},
        ),
    ]
    numbers = {
        "mini_rates": mini_rates,
        "econ_first_tok": (mean(ea_f, "tokens"), mean(ec_f, "tokens")),
        "econ_repeat_tok": (mean(ea_r, "tokens"), mean(ec_r, "tokens")),
        "econ_first_lat": (mean(ea_f, "latency_ms"), mean(ec_f, "latency_ms")),
        "econ_repeat_lat": (mean(ea_r, "latency_ms"), mean(ec_r, "latency_ms")),
        "econ_reuse": (0.0, sum(1 for r in ec_r if r["reused"]) / len(ec_r)),
        "con_rate": (identical_rate(con_a), identical_rate(con_c)),
        "repair_pass": (pass_rate(ra), pass_rate(rc)),
        "repair_tok": (mean(ra, "tokens"), mean(rc, "tokens")),
        "repair_lat": (mean(ra, "latency_ms"), mean(rc, "latency_ms")),
        "charts": [str(p) for p in charts],
    }
    print(json.dumps({k: v for k, v in numbers.items() if k != "charts"}, indent=1))
    for p in charts:
        print("chart:", p)
    return numbers


if __name__ == "__main__":
    main()
