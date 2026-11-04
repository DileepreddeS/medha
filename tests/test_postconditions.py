"""Postcondition DSL: every form incl. the spec's example contract."""

from medha.verify.postconditions import (
    check_all,
    check_one,
    check_spec_shape,
    match_expected,
    texts_of,
)

RESULT = {
    "totals": {"by_category": {"food": 60.0, "drink": 40.0}, "grand_total": 100.0},
    "n_rows": 10,
    "report_md": "/work/out/report.md",
}
FILES = {"report.md": b"# Report\n\nfood: 60\ndrink: 40\n"}


def test_spec_example_contract():
    rows = check_all(
        RESULT,
        FILES,
        [
            "sum(totals.by_category.values()) == totals.grand_total ± 0.01",
            "every category in totals.by_category appears in report_md",
        ],
    )
    assert [r["passed"] for r in rows] == [True, True], rows


def test_comparisons_and_outputs():
    rows = check_all(
        RESULT,
        FILES,
        [
            "n_rows == 10",
            "n_rows != 9",
            "n_rows > 5",
            "n_rows >= 10",
            "n_rows < 11",
            "n_rows <= 10",
            "output report.md exists",
        ],
    )
    assert all(r["passed"] for r in rows)


def test_output_exists_resolves_result_key_to_file():
    """`output report_md exists` follows result["report_md"] to its file.

    Regression: live skills return result keys pointing at files
    (report_md -> /work/out/report.md); the literal-filename-only check
    rejected a correct skill whose isolated tests all passed.
    """
    ok = check_one("output report_md exists", RESULT, texts_of(FILES))
    assert ok["passed"] is True, ok
    dangling = check_one(
        "output report_md exists", {"report_md": "/work/out/gone.md"}, texts_of(FILES)
    )
    assert dangling["passed"] is False, dangling


def test_failures_are_explicit():
    assert check_one("n_rows == 11", RESULT, texts_of(FILES))["passed"] is False
    assert check_one("output nope.csv exists", RESULT, texts_of(FILES))["passed"] is False
    bad = check_one("sum(nothing.here) == 1", RESULT, texts_of(FILES))
    assert bad["passed"] is False and "check error" in bad["detail"]
    unsupported = check_one("the report feels complete", RESULT, texts_of(FILES))
    assert unsupported["passed"] is False and "unsupported" in unsupported["detail"]


def test_disallowed_expressions():
    evil = check_one("__import__('os') == 1", RESULT, texts_of(FILES))
    assert evil["passed"] is False


def test_match_expected():
    assert (
        match_expected(
            RESULT,
            FILES,
            {
                "result_subset": {"n_rows": 10, "totals": {"grand_total": 100.0}},
                "files_present": ["report.md"],
            },
        )
        == []
    )
    fails = match_expected(
        RESULT,
        FILES,
        {
            "result_subset": {"n_rows": 11},
            "files_present": ["nope.md"],
        },
    )
    assert len(fails) == 2


def _outputs():
    from medha.store.models import IOSpec

    return {
        "top": IOSpec(type="list"),
        "total": IOSpec(type="number"),
        "report_md": IOSpec(type="file"),
        "note": IOSpec(type="string", optional=True),
        "weird": IOSpec(type="dataframe"),
    }


def test_spec_shape_accepts_conformant_result():
    rows = check_spec_shape(
        {"top": [{"item": "x"}], "total": 3.5, "report_md": "/work/out/r.md"}, _outputs()
    )
    assert all(r["passed"] for r in rows), rows


def test_spec_shape_rejects_path_where_list_wanted():
    """Regression: top_v1 registered a path-string under "top" and the gate blessed it."""
    rows = check_spec_shape(
        {"top": "/work/out/top.json", "total": 3.5, "report_md": "/w/r.md"}, _outputs()
    )
    bad = [r for r in rows if not r["passed"]]
    assert len(bad) == 1 and "top" in bad[0]["postcondition"], rows


def test_spec_shape_missing_required_fails_optional_skips_unknown_skips():
    rows = check_spec_shape({"top": []}, _outputs())
    by_post = {r["postcondition"]: r["passed"] for r in rows}
    assert by_post.get("result has total") is False
    assert by_post.get("result has report_md") is False
    assert not any("note" in p for p in by_post)
    assert not any("weird" in p for p in by_post)
