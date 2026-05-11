from cachetools import TTLCache
from threading import Lock
from app.models import BusinessConfig

# 5-minute TTL; holds up to 512 businesses in memory
_cache: TTLCache = TTLCache(maxsize=512, ttl=300)
_lock = Lock()


def get_cached_business(slug: str) -> BusinessConfig | None:
    with _lock:
        return _cache.get(slug)


def set_cached_business(slug: str, config: BusinessConfig) -> None:
    with _lock:
        _cache[slug] = config


def invalidate_business(slug: str) -> None:
    with _lock:
        _cache.pop(slug, None)
