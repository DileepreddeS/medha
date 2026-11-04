"""Deterministic bench checkers. FIREWALLED: only bench.run may use these."""

from bench.checkers import compose, invoice, owner, receipts, sales, stock, top

CHECKERS = {
    "sales_summary": sales.check,
    "top_items": top.check,
    "invoice_total": invoice.check,
    "receipt_categorization": receipts.check,
    "owner_summary": owner.check,
    "stock_alert": stock.check,
    "compose": compose.check,
}

__all__ = ["CHECKERS", "compose", "invoice", "owner", "receipts", "sales", "stock", "top"]
