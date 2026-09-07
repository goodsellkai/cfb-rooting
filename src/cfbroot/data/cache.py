"""A small TTL disk cache so repeated refreshes don't hammer the API.

Game results need a short TTL on Saturdays and a long one in the offseason, so
callers pass the TTL per fetch rather than configuring it globally.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from ..config import CACHE_DIR


def _path(namespace: str, key: dict) -> Path:
    digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    return CACHE_DIR / namespace / f"{digest}.json"


def get_or_fetch(namespace: str, key: dict, ttl_seconds: float,
                 fetch: Callable[[], Any], *, force: bool = False) -> Any:
    path = _path(namespace, key)
    if not force and path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - blob["fetched_at"] <= ttl_seconds:
                return blob["data"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # corrupt or partial cache entry -- just refetch

    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched_at": time.time(), "key": key, "data": data}),
                   encoding="utf-8")
    tmp.replace(path)
    return data


def cache_age(namespace: str, key: dict) -> float | None:
    """Seconds since this entry was written, or None if it isn't cached."""
    path = _path(namespace, key)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        return time.time() - blob["fetched_at"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def clear(namespace: str | None = None) -> int:
    root = CACHE_DIR / namespace if namespace else CACHE_DIR
    if not root.exists():
        return 0
    n = 0
    for p in root.rglob("*.json"):
        p.unlink()
        n += 1
    return n
