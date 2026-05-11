import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


def _clean_url(url: str) -> str:
    """asyncpg needs postgresql:// and handles SSL via the ssl= kwarg, not sslmode query param."""
    url = url.replace("postgres://", "postgresql://", 1)
    if "?" in url:
        base, qs = url.split("?", 1)
        kept = {k: v for p in qs.split("&") if "=" in p for k, v in [p.split("=", 1)]}
        kept.pop("sslmode", None)
        url = base + ("?" + "&".join(f"{k}={v}" for k, v in kept.items()) if kept else "")
    return url


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        _clean_url(settings.database_url),
        min_size=settings.db_min_connections,
        max_size=settings.db_max_connections,
        ssl="require",
        command_timeout=30,
        statement_cache_size=0,  # required for PgBouncer / Neon pooled endpoints
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool
