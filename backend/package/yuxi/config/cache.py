"""运行时配置 Redis 快照同步。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

from yuxi.storage.redis import RedisConfig, sync_redis_client
from yuxi.utils.logging_config import logger

RUNTIME_CONFIG_REDIS_KEY = "yuxi:runtime_config"
RUNTIME_CONFIG_SYNC_INTERVAL_SECONDS = 5.0
_RUNTIME_CONFIG_REDIS_TIMEOUT_SECONDS = 0.2


def _runtime_config_redis_config() -> RedisConfig:
    return RedisConfig.from_env(
        decode_responses=True,
        socket_timeout=_RUNTIME_CONFIG_REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=_RUNTIME_CONFIG_REDIS_TIMEOUT_SECONDS,
    )


def _runtime_fields(config: Any) -> Iterator[str]:
    for field_name, field_info in type(config).model_fields.items():
        if field_info.exclude:
            continue
        yield field_name


def _runtime_snapshot(config: Any) -> dict[str, Any]:
    return {field_name: getattr(config, field_name) for field_name in _runtime_fields(config)}


def _load_snapshot() -> dict[str, Any] | None:
    try:
        with sync_redis_client(_runtime_config_redis_config()) as redis_client:
            raw = redis_client.get(RUNTIME_CONFIG_REDIS_KEY)
    except Exception as e:
        logger.warning(f"Failed to load runtime config from Redis: {e}")
        return None

    if not raw:
        return None

    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to decode runtime config snapshot: {e}")
        return None

    return snapshot if isinstance(snapshot, dict) else None


def save_runtime_config(config: Any) -> None:
    try:
        with sync_redis_client(_runtime_config_redis_config()) as redis_client:
            redis_client.set(
                RUNTIME_CONFIG_REDIS_KEY,
                json.dumps(_runtime_snapshot(config), ensure_ascii=False),
            )
    except Exception as e:
        logger.warning(f"Failed to save runtime config to Redis: {e}")


def refresh_runtime_config(config: Any) -> None:
    snapshot = _load_snapshot()
    if snapshot is None:
        return

    for field_name in _runtime_fields(config):
        if field_name in snapshot:
            setattr(config, field_name, snapshot[field_name])


def start_runtime_sync(
    config: Any,
    current_thread: threading.Thread | None,
    *,
    interval: float = RUNTIME_CONFIG_SYNC_INTERVAL_SECONDS,
) -> threading.Thread:
    if current_thread is not None:
        return current_thread

    def runtime_sync_loop() -> None:
        while True:
            time.sleep(interval)
            try:
                refresh_runtime_config(config)
            except Exception as e:
                logger.warning(f"Runtime config sync iteration failed: {e}")

    thread = threading.Thread(target=runtime_sync_loop, name="config-runtime-sync", daemon=True)
    thread.start()
    logger.info(f"Runtime config sync thread started (interval={interval}s)")
    return thread
