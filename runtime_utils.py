import asyncio
import json
import os
import tempfile
import time
from typing import Any, Callable

import httpx


class LogThrottle:
    def __init__(self, interval_seconds: int = 300):
        self.interval_seconds = max(1, int(interval_seconds))
        self._last_log_time: dict[str, float] = {}
        self._suppressed_count: dict[str, int] = {}

    def log(self, key: str, log_func: Callable[[str], None], message: str) -> None:
        now = time.time()
        last_time = self._last_log_time.get(key, 0.0)
        if now - last_time >= self.interval_seconds:
            suppressed = self._suppressed_count.pop(key, 0)
            if suppressed > 0:
                log_func(f"{message}（期间抑制重复日志 {suppressed} 次）")
            else:
                log_func(message)
            self._last_log_time[key] = now
            return
        self._suppressed_count[key] = self._suppressed_count.get(key, 0) + 1


def load_json_file(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
    except Exception:
        return default
    return default


def save_json_file(path: str, data: Any) -> bool:
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
        return True
    except Exception:
        return False


def normalize_api_keys(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, str):
        return [item.strip() for item in raw_value.split(",") if item.strip()]
    return []


async def fetch_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> bytes | None:
    async def _request() -> bytes | None:
        response = await client.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.content
        return None

    if semaphore is None:
        return await _request()
    async with semaphore:
        return await _request()


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[int, Any | None]:
    async def _request() -> tuple[int, Any | None]:
        response = await client.get(url, params=params, headers=headers, timeout=timeout)
        try:
            return response.status_code, response.json()
        except Exception:
            return response.status_code, None

    if semaphore is None:
        return await _request()
    async with semaphore:
        return await _request()


async def download_to_path(
    client: httpx.AsyncClient,
    url: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> str | None:
    content = await fetch_bytes(client, url, headers=headers, timeout=timeout, semaphore=semaphore)
    if not content:
        return None
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as file:
        file.write(content)
    return path


def create_temp_png(content: bytes) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as file:
        file.write(content)
        return file.name


def safe_remove(path: str | None) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        return