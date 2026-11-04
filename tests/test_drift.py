"""Drift: schema diffs and resemblance checks."""

from medha.core import drift

OLD = {"pos.csv": b"date,amount,item\n2026-01-01,5,x\n"}
NEW_COL = {"pos.csv": b"date,amount,item,store\n2026-01-01,5,x,s1\n"}
RENAMED = {"pos.csv": b"day,total,product\n2026-01-01,5,x\n"}


def test_describe_diff():
    diff = drift.describe_diff(OLD, NEW_COL)
    assert "new column 'store'" in diff
    diff2 = drift.describe_diff(OLD, RENAMED)
    assert "dropped column 'date'" in diff2 and "new column 'day'" in diff2
    assert drift.describe_diff(OLD, dict(OLD)) == "no schema differences detected"
    assert "new file" in drift.describe_diff(OLD, {**OLD, "extra.csv": b"a"})


def test_resembles():
    assert drift.resembles(OLD, NEW_COL) is True
    assert drift.resembles(OLD, RENAMED) is False  # total rename
    assert drift.resembles(OLD, {"data.pdf": b"%PDF"}) is False
    assert drift.resembles({}, NEW_COL) is False
