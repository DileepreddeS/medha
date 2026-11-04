"""Bearer auth + per-IP rate limit. Fail closed when no token is configured."""

from __future__ import annotations

import hmac
import time
from typing import Any

from fastapi import HTTPException, Request

_hits: dict[str, list[float]] = {}


def make_auth(settings: Any):
    async def require_token(request: Request) -> None:
        token = settings.MEDHA_TOKEN or ""
        if not token or token == "change-me-before-deploy":
            raise HTTPException(status_code=500, detail="server token not configured")
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="unauthorized")

    return require_token


def make_rate_limit(limit: int = 240, window_s: int = 60):
    async def rate_limit(request: Request) -> None:
        ip = request.client.host if request.client else "?"
        now = time.monotonic()
        bucket = [t for t in _hits.get(ip, []) if now - t < window_s]
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="rate limited")
        bucket.append(now)
        _hits[ip] = bucket

    return rate_limit


def reset_rate_limit() -> None:
    _hits.clear()
