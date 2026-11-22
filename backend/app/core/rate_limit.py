"""
Per-tenant (and per-IP for unauthenticated routes) rate limiting backed by Redis.

Strategy: sliding-window counter using a Redis sorted set per key.
- Each hit is stored as a member with score = timestamp (ms).
- Before recording a hit we remove members older than the window.
- The count of remaining members is the number of requests in the window.

This is O(log N) per request and survives API restarts (state in Redis).

Usage in routes:
    from app.core.rate_limit import rate_limit
    await rate_limit(request, key="upload", limit=10, window_seconds=60)
"""

import time
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Lazily initialised — one pool shared by all coroutines
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


# ── Sliding-window implementation ─────────────────────────────────────────────

async def _sliding_window_check(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """
    Returns (allowed, current_count).
    allowed=False means the limit is exceeded.
    """
    redis = _get_redis()
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    oldest_allowed = now_ms - window_ms

    pipe = redis.pipeline()
    # Remove stale entries
    pipe.zremrangebyscore(key, "-inf", oldest_allowed)
    # Count current window
    pipe.zcard(key)
    # Add this request
    pipe.zadd(key, {str(now_ms): now_ms})
    # Expire the key so Redis cleans up idle keys automatically
    pipe.expire(key, window_seconds * 2)
    results = await pipe.execute()

    current_count: int = results[1]  # count before adding this request
    allowed = current_count < limit
    return allowed, current_count


# ── Rate limit configs (name → (limit, window_seconds)) ───────────────────────

RATE_LIMITS: dict[str, tuple[int, int]] = {
    # endpoint key    limit  window
    "upload":        (20,   3600),   # 20 uploads / tenant / hour
    "login":         (10,   300),    # 10 login attempts / IP / 5 min
    "register":      (5,    3600),   # 5 registrations / IP / hour
    "api":           (300,  60),     # 300 general API calls / tenant / min
    "chat":          (30,   60),     # 30 chat requests / tenant / min
    "refresh":       (20,   3600),   # 20 token refreshes / user / hour
    "batch_upload":  (5,    3600),   # 5 batch uploads / tenant / hour
}


async def rate_limit(
    request: Request,
    endpoint: str,
    *,
    tenant_id: str | None = None,
) -> None:
    """
    FastAPI dependency-compatible rate limiter.

    The key is scoped:
    - Authenticated routes: tenant_id  (shared bucket for the whole org)
    - Unauthenticated routes: client IP

    Raises HTTP 429 if the limit is exceeded.
    """
    if endpoint not in RATE_LIMITS:
        return  # No limit configured — allow

    limit, window = RATE_LIMITS[endpoint]

    # Build the bucket key
    if tenant_id:
        bucket = f"rl:{endpoint}:tenant:{tenant_id}"
    else:
        # For unauthenticated endpoints use the real client IP
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.client.host  # type: ignore[union-attr]
        )
        bucket = f"rl:{endpoint}:ip:{client_ip}"

    try:
        allowed, count = await _sliding_window_check(bucket, limit, window)
    except Exception as exc:
        # Redis down → fail open (don't block users for infra issues)
        logger.warning("rate_limit_redis_error", error=str(exc), endpoint=endpoint)
        return

    if not allowed:
        logger.warning(
            "rate_limit_exceeded",
            endpoint=endpoint,
            bucket=bucket,
            count=count,
            limit=limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per {window}s for '{endpoint}'.",
            headers={"Retry-After": str(window)},
        )
