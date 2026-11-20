from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

import structlog

from app.core.config import get_settings

logger = structlog.get_logger("rate_limit")
settings = get_settings()

_redis: Any = None
_redis_failed = False
_local: dict[str, deque[float]] = defaultdict(deque)
_lock = asyncio.Lock()
_last_prune = 0.0


async def _get_redis():
    global _redis, _redis_failed
    if _redis is not None or _redis_failed or not settings.redis_url:
        return _redis
    try:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1)
        await _redis.ping()
    except Exception as exc:  # noqa: BLE001
        _redis_failed = True
        logger.warning("redis_unavailable_rate_limit_fallback", error=str(exc))
        _redis = None
    return _redis


async def hit(key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Fixed-window limiter. Returns (allowed, remaining).

    Redis is shared across API replicas. A bounded in-process fallback keeps
    brute-force protection working (per replica) when Redis is unavailable.
    """
    client = await _get_redis()
    if client is not None:
        try:
            window = int(time.time() // window_seconds)
            redis_key = f"rl:{key}:{window}"
            pipe = client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            count, _ = await pipe.execute()
            return int(count) <= limit, max(0, limit - int(count))
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis_rate_limit_error", error=str(exc))

    async with _lock:
        now = time.monotonic()
        bucket = _local[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False, 0
        bucket.append(now)
        _prune_local(now)
        return True, limit - len(bucket)


def _prune_local(now: float) -> None:
    """Keep the in-process fallback bounded under key floods.

    Previously only empty buckets were removed, so one entry per attacker-chosen
    email/IP stayed forever. Pruning is rate limited so a flood cannot turn every
    request into a full-table sweep: drop buckets whose newest hit is older than
    the longest configured window, then evict the oldest inserted keys if the
    hard cap is still exceeded.
    """
    global _last_prune
    if len(_local) <= 10_000 or now - _last_prune < 5:
        return
    _last_prune = now
    stale_cutoff = now - 3_600
    for key in [key for key, bucket in _local.items() if not bucket or bucket[-1] <= stale_cutoff]:
        del _local[key]
    if len(_local) > 50_000:
        for key in list(_local)[: len(_local) // 10]:
            _local.pop(key, None)


async def reset(key: str, *, window_seconds: int = 60) -> None:
    client = await _get_redis()
    if client is not None:
        try:
            await client.delete(f"rl:{key}:{int(time.time() // max(window_seconds, 1))}")
        except Exception:  # noqa: BLE001, S110 - cache cleanup is best-effort
            pass
    _local.pop(key, None)


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
