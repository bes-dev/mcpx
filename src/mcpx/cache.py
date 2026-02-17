import json
import time
from pathlib import Path
from typing import Any

from mcpx.config import CONFIG_DIR

CACHE_DIR = CONFIG_DIR / "cache"
TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _cache_path(alias: str) -> Path:
    return CACHE_DIR / f"{alias}.json"


def load_cached_tools(alias: str) -> list[dict[str, Any]] | None:
    path = _cache_path(alias)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if time.time() - data.get("timestamp", 0) > TTL_SECONDS:
        path.unlink(missing_ok=True)
        return None
    return data.get("tools")


def save_tools_cache(alias: str, tools: list[dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"timestamp": time.time(), "tools": tools}
    _cache_path(alias).write_text(json.dumps(data, indent=2))


def invalidate_cache(alias: str) -> None:
    _cache_path(alias).unlink(missing_ok=True)
