"""MedhaBench reporting: console table + matplotlib PNGs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def print_table(report: dict[str, Any]) -> None:
    print(f"MedhaBench {report.get('ts')} ({report.get('suite')})")
    print(f"{'config':<8} {'pass':>6} {'n':>3} {'$/task':>8} {'tok/task':>9} {'reuse':>6}")
    for name, cfg in (report.get("configs") or {}).items():
        s = cfg["summary"]
        print(
            f"{name:<8} {s['pass_rate']:>6.0%} {s['n']:>3} "
            f"{s['cost_per_task']:>8.4f} {s['tokens_per_task']:>9.0f} {s['reuse_rate']:>6.0%}"
        )


def write_charts(report: dict[str, Any], out_dir: str | Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    cfgs = report.get("configs") or {}
    names = [n for n in ("a", "b", "c", "d") if n in cfgs]
    paths: list[str] = []
    if names:
        rates = [cfgs[n]["summary"]["pass_rate"] for n in names]
        fig, ax = plt.subplots()
        ax.bar(names, rates)
        ax.set_ylim(0, 1)
        ax.set_title("Pass rate by config")
        ax.set_ylabel("pass rate")
        p = out_dir / "pass_rate.png"
        fig.savefig(p)
        plt.close(fig)
        paths.append(str(p))
    if "a" in cfgs and "c" in cfgs:
        cum_a, cum_c, ta, tc = [], [], 0.0, 0.0
        for r in cfgs["a"]["rows"]:
            ta += r["cost_usd"]
            cum_a.append(ta)
        for r in cfgs["c"]["rows"]:
            tc += r["cost_usd"]
            cum_c.append(tc)
        fig, ax = plt.subplots()
        ax.plot(cum_a, label="A baseline")
        ax.plot(cum_c, label="C medha cold")
        ax.set_title("Cumulative $ over the task sequence")
        ax.set_xlabel("task")
        ax.set_ylabel("$")
        ax.legend()
        p = out_dir / "cost_over_time.png"
        fig.savefig(p)
        plt.close(fig)
        paths.append(str(p))
    if "repair" in cfgs:
        rows = cfgs["repair"]["rows"]
        labels = [r["id"] for r in rows]
        vals = [1 if r["passed"] else 0 for r in rows]
        fig, ax = plt.subplots()
        ax.bar(labels, vals)
        ax.set_ylim(0, 1)
        ax.set_title("Repair suite outcomes")
        plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
        p = out_dir / "repair.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths.append(str(p))
    return paths
