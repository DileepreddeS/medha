"""Shared fixtures: isolated Settings + stub LLM backend. No network, no Modal."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from medha.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    # Override every secret/network field: tests must never read the real .env
    # (a failure repr once leaked the real HF_TOKEN into test output).
    return Settings(
        META_API_KEY="LLM|0|test",
        HF_TOKEN="",
        MEDHA_TOKEN="change-me-before-deploy",
        GLIMMER_BASE_URL="http://glimmer.test/v1",
        GLIMMER_API_KEY="",
        # Suite tests spec behavior (Glimmer authors); fallback tests opt
        # into "meta" explicitly. Also keeps stubbed calls off the network.
        LLM_AUTHOR_PROVIDER="glimmer",
        MEDHA_DB_PATH=str(tmp_path / "medha.db"),
        MEDHA_SKILLS_DIR=str(tmp_path / "skills"),
        MEDHA_VERIFY_DIR=str(tmp_path / "verify_store"),
        LLM_CACHE_DIR=str(tmp_path / "llm_cache"),
        COST_LOG_PATH=str(tmp_path / "cost.jsonl"),
        MEDHA_BENCH_RESULTS=str(tmp_path / "bench_results"),
        DAILY_SPEND_CAP_USD=5.0,
        DEMO_MODE=False,
    )


class StubBackend:
    """Scripted ChatBackend. responses: list of (content, in, out) or Exception."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, *, model, messages, max_tokens, temperature, json_mode):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "json_mode": json_mode,
            }
        )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def stub_backend():
    return StubBackend([("pong", 10, 5)])
