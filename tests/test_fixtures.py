"""Fixture consistency: every yaml `expected` recomputed from the CSV bytes.

Guards hand arithmetic in bench fixtures without needing live LLM runs.
"""

import csv
from pathlib import Path

import yaml

from bench.pdfgen import make_pdf

REPO = Path(__file__).resolve().parent.parent
TASKS_YAML = REPO / "bench" / "tasks" / "mini.yaml"
FIXTURES = REPO / "bench" / "fixtures"


def _load():
    return yaml.safe_load(TASKS_YAML.read_text())


def _csv(name):
    with (FIXTURES / name).open(newline="") as f:
        return list(csv.DictReader(f))


def _amount(row):
    for key in ("amount", "gross_amount", "total_usd", "revenue", "sales"):
        if key in row and row[key] not in (None, ""):
            return float(str(row[key]).replace("$", ""))
    raise KeyError(f"no amount column in {row}")


def _resolve(task):
    """Map task file refs to real bytes (CSVs from disk, PDFs generated)."""
    out = {}
    for _logical, fname in task["files"].items():
        if fname.endswith(".pdf"):
            out[fname] = make_pdf(task.get("pdf_lines", []))
        else:
            out[fname] = (FIXTURES / fname).read_bytes()
    return out


def _all_tasks():
    doc = _load()
    return doc["tasks"] + doc["repair"] + doc["heldout"] + doc["compose"]


def test_every_task_file_resolves():
    for task in _all_tasks():
        files = _resolve(task)
        assert files, task["id"]
        for name, blob in files.items():
            assert len(blob) > 0, (task["id"], name)


def test_sales_expected_matches_csv():
    sales_tasks = [t for t in _all_tasks() if t["family"] == "sales_summary"]
    assert len(sales_tasks) == 2 + 3 + 1  # tasks + repair + heldout
    for task in sales_tasks:
        fname = next(iter(task["files"].values()))
        rows = _csv(fname)
        by_cat: dict[str, float] = {}
        for r in rows:
            cat = r.get("category") or r.get("cat")
            by_cat[cat] = by_cat.get(cat, 0.0) + _amount(r)
        exp = task["expected"]
        assert set(by_cat) == set(exp["by_category"]), task["id"]
        for cat, want in exp["by_category"].items():
            assert abs(by_cat[cat] - want) < 0.005, (task["id"], cat)
        assert abs(sum(by_cat.values()) - exp["grand_total"]) < 0.005, task["id"]


def test_top_expected_matches_csv():
    for task in _all_tasks():
        if task["family"] != "top_items":
            continue
        fname = next(iter(task["files"].values()))
        rows = _csv(fname)
        name_key = "item" if "item" in rows[0] else "product"
        ranked = sorted(rows, key=_amount, reverse=True)[:2]
        for got, want in zip(ranked, task["expected"]["top"], strict=True):
            assert got[name_key] == want["item"], task["id"]
            assert abs(_amount(got) - want["revenue"]) < 0.005, task["id"]


def test_stock_expected_matches_csv():
    for task in _all_tasks():
        if task["family"] != "stock_alert":
            continue
        fname = next(iter(task["files"].values()))
        low = sorted(r["item"] for r in _csv(fname) if float(r["qty"]) <= float(r["reorder_at"]))
        assert low == sorted(task["expected"]["low_stock"]), task["id"]


def test_receipts_rules_match_csv():
    rules = {
        "receipts_v1.csv": ({"Whole Foods": "groceries", "Shell": "fuel"}),
        "receipts_v2.csv": ({"Trader Joes": "groceries", "BP": "fuel"}),
        "receipts_v3.csv": ({"Whole Foods": "groceries", "Shell": "fuel"}),
    }
    for task in _all_tasks():
        if task["family"] != "receipt_categorization":
            continue
        fname = next(iter(task["files"].values()))
        mapping = rules[fname]
        got = []
        for r in _csv(fname):
            matches = [cat for vendor, cat in mapping.items() if vendor in r["vendor"]]
            assert len(matches) == 1, (task["id"], r)
            got.append(matches[0])
        assert got == task["expected"]["categories"], task["id"]
        for vendor in mapping:
            assert vendor in task["goal"], (task["id"], vendor)  # rule stated in goal


def test_owner_needles_derive_from_csv():
    for task in _all_tasks():
        if task["family"] != "owner_summary":
            continue
        fname = next(iter(task["files"].values()))
        rows = _csv(fname)
        date_key = "date" if "date" in rows[0] else "day"
        cat_key = "category" if "category" in rows[0] else "cat"
        dates = [r[date_key] for r in rows]
        grand = sum(_amount(r) for r in rows)
        by_cat: dict[str, float] = {}
        for r in rows:
            by_cat[r[cat_key]] = by_cat.get(r[cat_key], 0.0) + _amount(r)
        top_cat = max(by_cat, key=by_cat.get)  # type: ignore[arg-type]
        needles = task["expected"]["contains"]
        assert needles[0] in dates and needles[1] in dates, task["id"]
        assert abs(float(needles[2]) - grand) < 0.05, task["id"]
        assert needles[3] == top_cat, task["id"]


def test_invoice_total_matches_pdf_lines():
    for task in _all_tasks():
        if task["family"] != "invoice_total":
            continue
        totals = [ln for ln in task["pdf_lines"] if ln.startswith("TOTAL:")]
        assert len(totals) == 1, task["id"]
        assert abs(float(totals[0].split("TOTAL:")[1]) - task["expected"]["total"]) < 0.005


def test_compose_parts_reference_real_families():
    from bench.checkers import CHECKERS

    for task in _all_tasks():
        if task["family"] != "compose":
            continue
        for family in task["expected"]["parts"]:
            assert family in CHECKERS, (task["id"], family)
