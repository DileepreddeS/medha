"""Vendored BM25 fallback: ranking sanity (used when rank-bm25 is missing)."""

from medha.core._bm25 import BM25Okapi


def test_ranking_orders_by_relevance():
    docs = [["sales", "csv", "report"], ["sales", "sales", "sales", "csv"], ["invoice", "pdf"]]
    scores = BM25Okapi(docs).get_scores(["sales", "csv"])
    assert scores[1] > scores[0] > scores[2]


def test_unknown_terms_score_zero():
    assert BM25Okapi([["a", "b"]]).get_scores(["zzz"]) == [0.0]


def test_router_resolves_a_backend():
    import medha.core.router as router_mod

    assert hasattr(router_mod.BM25Okapi, "get_scores")
