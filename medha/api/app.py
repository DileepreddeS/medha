"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from medha.api.routes import Context, build_routers
from medha.config import get_settings
from medha.llm.client import LLMClient
from medha.sandbox.runner import ModalSandboxRunner
from medha.store.db import Database


def create_app(
    settings: Any = None,
    db: Database | None = None,
    runner: Any = None,
    make_llm: Callable[[], LLMClient] | None = None,
    commit: Callable[[], None] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    db = db or Database(settings.MEDHA_DB_PATH)
    runner = runner or ModalSandboxRunner()
    make_llm = make_llm or (lambda: LLMClient(settings))
    ctx = Context(settings=settings, db=db, runner=runner, make_llm=make_llm, commit=commit)
    app = FastAPI(title="Medha")
    public, protected = build_routers(ctx)
    app.include_router(public)
    app.include_router(protected)
    dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    return app
