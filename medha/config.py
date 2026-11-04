"""Typed settings. The only sanctioned global state (see AGENTS.md)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # secrets (mirrored in Modal Secret "medha-secrets")
    META_API_KEY: str = ""
    HF_TOKEN: str = ""
    MEDHA_TOKEN: str = "change-me-before-deploy"
    # providers
    GLIMMER_BASE_URL: str = ""
    GLIMMER_API_KEY: str = ""
    GLIMMER_MODEL_ID: str = "meta-models/Muse-Glimmer-30B"
    GLIMMER_SERVED_MODEL: str = "glimmer"
    # Which provider serves author-side roles (planner/router/
    # author/testwriter/repairer/composer). Glimmer live since 2026-11-03
    # (one.yaml learn+reuse green); "meta" = Spark 1.3 fallback (D2).
    # Judge/escalation are always "meta" (see ROLE_PROVIDER).
    LLM_AUTHOR_PROVIDER: Literal["glimmer", "meta"] = "glimmer"
    # Glimmer scale-to-zero: retry 502/503/504 (cold boot) this long.
    GLIMMER_RETRY_BUDGET_S: float = 360.0
    # Glimmer deploy idle window (read at `modal deploy` time).
    GLIMMER_SCALEDOWN_WINDOW_S: int = 900
    META_BASE_URL: str = "https://api.meta.ai/v1"
    META_MODEL_ID: str = "muse-spark-1.3"
    META_PRICE_IN_PER_1K: float = 0.0
    META_PRICE_OUT_PER_1K: float = 0.0
    # budget guards
    DAILY_SPEND_CAP_USD: float = 5.0
    MAX_TOKENS_PER_TASK: int = 60000
    ALLOW_ESCALATION: bool = False
    DEMO_MODE: bool = False
    # thresholds
    ROUTE_MIN: float = 0.6
    HOLDOUT_MIN: float = 0.8
    # storage
    MEDHA_DB_PATH: str = "./medha_data/medha.db"
    MEDHA_SKILLS_DIR: str = "./medha_data/skills"
    MEDHA_VERIFY_DIR: str = "./medha_data/verify_store"
    LLM_CACHE_DIR: str = "./fixtures/llm_cache"
    COST_LOG_PATH: str = "./medha_data/cost.jsonl"
    MEDHA_BENCH_RESULTS: str = "bench/results"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
