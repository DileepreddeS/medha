"""Minimal BM25-Okapi, written fresh from Robertson/Zaragoza's formula.

Fallback when the ``rank-bm25`` package is unavailable (e.g. offline
environments). Same interface subset (``get_scores``), same k1/b defaults.
This is an original implementation of the published formula, not copied code.
"""

from __future__ import annotations

import math
from collections import Counter


class BM25Okapi:
    def __init__(
        self,
        corpus: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
        epsilon: float = 0.25,
    ) -> None:
        self.corpus_size = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_len) / max(len(corpus), 1)
        self.k1, self.b = k1, b
        df: dict[str, int] = {}
        for doc in corpus:
            for word in set(doc):
                df[word] = df.get(word, 0) + 1
        self.idf: dict[str, float] = {}
        for word, freq in df.items():
            self.idf[word] = math.log(self.corpus_size - freq + 0.5) - math.log(freq + 0.5)
        avg_idf = sum(self.idf.values()) / max(len(self.idf), 1)
        self.floor = epsilon * avg_idf
        self.doc_freqs = [Counter(doc) for doc in corpus]

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            if idf < 0:
                idf = self.floor
            for i, freqs in enumerate(self.doc_freqs):
                f = freqs.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avgdl or 1))
                scores[i] += idf * f * (self.k1 + 1) / denom
        return scores
