import structlog
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_redis

router = APIRouter(tags=["health"])
log = structlog.get_logger()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness. Must not touch any dependency."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, str]:
    """Readiness. Checks the two things the app cannot serve without."""
    database = "ok"
    cache = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        log.exception("readyz_database_failed")
        database = "error"
    try:
        await redis.ping()
    except Exception:
        log.exception("readyz_redis_failed")
        cache = "error"
    status = "ok" if database == "ok" and cache == "ok" else "degraded"
    return {"status": status, "database": database, "redis": cache}
