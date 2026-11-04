"""Checkers: each family passes good output and rejects bad output."""

from bench.checkers import CHECKERS

SALES_EXP = {"by_category": {"drink": 7.00, "food": 9.00}, "grand_total": 16.00}
SALES_RES = {"totals": {"by_category": {"drink": 7.0, "food": 9.0}, "grand_total": 16.0}}
SALES_FILES = {"report.md": b"# Report\ndrink: 7\nfood: 9\n"}


def test_all_families_registered():
    assert set(CHECKERS) == {
        "sales_summary",
        "top_items",
        "invoice_total",
        "receipt_categorization",
        "owner_summary",
        "stock_alert",
        "compose",
    }


def test_compose_runs_part_checkers():
    exp = {"parts": {"invoice_total": {"total": 10.0}, "stock_alert": {"low_stock": ["X"]}}}
    good = {"total": 10.0, "low_stock": ["X"]}
    assert CHECKERS["compose"](good, {}, exp) == []
    bad = CHECKERS["compose"]({"total": 1.0, "low_stock": []}, {}, exp)
    assert len(bad) == 2 and bad[0].startswith("invoice_total:")


def test_sales():
    assert CHECKERS["sales_summary"](SALES_RES, SALES_FILES, SALES_EXP) == []
    bad = {"totals": {"by_category": {"drink": 7.0}, "grand_total": 7.0}}
    assert len(CHECKERS["sales_summary"](bad, SALES_FILES, SALES_EXP)) >= 2
    assert CHECKERS["sales_summary"](SALES_RES, {}, SALES_EXP)  # no report


def test_top():
    exp = {"top": [{"item": "A", "revenue": 10.0}, {"item": "B", "revenue": 5.0}]}
    assert (
        CHECKERS["top_items"](
            {"top": [{"item": "A", "revenue": 10}, {"item": "B", "revenue": 5.0}]}, {}, exp
        )
        == []
    )
    assert CHECKERS["top_items"](
        {"top": [{"item": "B", "revenue": 5.0}, {"item": "A", "revenue": 10.0}]}, {}, exp
    )


def test_invoice():
    assert CHECKERS["invoice_total"]({"total": 214.5}, {}, {"total": 214.50}) == []
    assert CHECKERS["invoice_total"]({"total": "214.50"}, {}, {"total": 214.50}) == []
    assert CHECKERS["invoice_total"]({"total": 100}, {}, {"total": 214.50})
    assert CHECKERS["invoice_total"]({}, {}, {"total": 1})


def test_receipts():
    exp = {"categories": ["groceries", "fuel"]}
    assert CHECKERS["receipt_categorization"]({"categories": ["groceries", "fuel"]}, {}, exp) == []
    assert CHECKERS["receipt_categorization"]({"categories": ["fuel", "groceries"]}, {}, exp)


def test_owner():
    exp = {"contains": ["2026-10-26", "16", "food"]}
    good = CHECKERS["owner_summary"]({}, {"s.md": b"range 2026-10-26..x total 16 top food"}, exp)
    assert good == []
    assert CHECKERS["owner_summary"]({}, {"s.md": b"nothing here"}, exp)


def test_stock():
    exp = {"low_stock": ["Milk", "Cups"]}
    assert CHECKERS["stock_alert"]({"low_stock": ["Cups", "Milk"]}, {}, exp) == []
    assert CHECKERS["stock_alert"]({"low_stock": ["Milk"]}, {}, exp)
