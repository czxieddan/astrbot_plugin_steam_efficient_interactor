import os
import time
from pathlib import Path

import httpx
from PIL import Image

from astrbot.api import logger

from ..runtime_utils import download_to_path, fetch_json


def ensure_path(value) -> Path:
    return value if isinstance(value, Path) else Path(value)


def get_font_path(font_name, base_dir=None):
    base_path = Path(base_dir or os.path.dirname(__file__))
    fonts_dir = base_path / "fonts"
    font_path = fonts_dir / font_name
    if font_path.exists():
        return str(font_path)
    fallback_path = base_path / font_name
    if fallback_path.exists():
        return str(fallback_path)
    return font_name


def render_gradient_bg(img_w, img_h, color_top, color_bottom):
    image = Image.new("RGB", (img_w, img_h), color_top)
    top_r, top_g, top_b = color_top
    bottom_r, bottom_g, bottom_b = color_bottom
    pixels = image.load()
    for y in range(img_h):
        ratio = y / (img_h - 1) if img_h > 1 else 0
        red = int(top_r * (1 - ratio) + bottom_r * ratio)
        green = int(top_g * (1 - ratio) + bottom_g * ratio)
        blue = int(top_b * (1 - ratio) + bottom_b * ratio)
        for x in range(img_w):
            pixels[x, y] = (red, green, blue)
    return image


async def get_avatar_path(
    data_dir,
    steamid,
    url,
    force_update=False,
    http_client: httpx.AsyncClient | None = None,
    request_semaphore=None,
):
    if not url:
        return None
    avatar_dir = ensure_path(data_dir) / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    path = avatar_dir / f"{steamid}.jpg"
    refresh_interval = 24 * 3600
    if path.exists() and not force_update:
        if time.time() - path.stat().st_mtime < refresh_interval:
            return str(path)
    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            return await download_to_path(client, url, str(path), timeout=10)
    return await download_to_path(http_client, url, str(path), timeout=10, semaphore=request_semaphore)


async def _search_sgdb_cover_url(
    client: httpx.AsyncClient,
    candidate_name: str,
    *,
    headers: dict,
    request_semaphore=None,
) -> str | None:
    search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{candidate_name}"
    status_code, data = await fetch_json(
        client,
        search_url,
        headers=headers,
        timeout=10,
        semaphore=request_semaphore,
    )
    if status_code != 200 or not data or not data.get("success") or not data.get("data"):
        return None

    sgdb_game_id = data["data"][0]["id"]
    grid_url = f"https://www.steamgriddb.com/api/v2/grids/game/{sgdb_game_id}?dimensions=600x900&type=static&limit=3"
    status_code, grid_data = await fetch_json(
        client,
        grid_url,
        headers=headers,
        timeout=10,
        semaphore=request_semaphore,
    )
    if status_code != 200 or not grid_data or not grid_data.get("success") or not grid_data.get("data"):
        return None

    for grid in grid_data["data"]:
        if grid.get("type") == "static" and grid.get("url"):
            return grid["url"]
    return grid_data["data"][0].get("url")


async def _resolve_sgdb_name_from_appid(
    client: httpx.AsyncClient,
    appid,
    *,
    headers: dict,
    request_semaphore=None,
) -> str | None:
    if not appid:
        return None
    game_url = f"https://www.steamgriddb.com/api/v2/games/steam/{appid}"
    status_code, data = await fetch_json(
        client,
        game_url,
        headers=headers,
        timeout=10,
        semaphore=request_semaphore,
    )
    if status_code != 200 or not data or not data.get("success") or not data.get("data"):
        return None
    return data["data"].get("name")


async def _get_sgdb_vertical_cover_with_client(
    client: httpx.AsyncClient,
    game_name,
    *,
    sgdb_api_key=None,
    sgdb_game_name=None,
    appid=None,
    request_semaphore=None,
) -> str | None:
    if not sgdb_api_key:
        return None

    headers = {"Authorization": f"Bearer {sgdb_api_key}"}
    search_name = sgdb_game_name or game_name
    if not search_name:
        return None

    cover_url = await _search_sgdb_cover_url(
        client,
        search_name,
        headers=headers,
        request_semaphore=request_semaphore,
    )
    if cover_url:
        return cover_url

    resolved_name = await _resolve_sgdb_name_from_appid(
        client,
        appid,
        headers=headers,
        request_semaphore=request_semaphore,
    )
    if not resolved_name:
        return None

    return await _search_sgdb_cover_url(
        client,
        resolved_name,
        headers=headers,
        request_semaphore=request_semaphore,
    )


async def get_sgdb_vertical_cover(
    game_name,
    sgdb_api_key=None,
    sgdb_game_name=None,
    appid=None,
    http_client: httpx.AsyncClient | None = None,
    request_semaphore=None,
):
    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            return await _get_sgdb_vertical_cover_with_client(
                client,
                game_name,
                sgdb_api_key=sgdb_api_key,
                sgdb_game_name=sgdb_game_name,
                appid=appid,
                request_semaphore=request_semaphore,
            )
    return await _get_sgdb_vertical_cover_with_client(
        http_client,
        game_name,
        sgdb_api_key=sgdb_api_key,
        sgdb_game_name=sgdb_game_name,
        appid=appid,
        request_semaphore=request_semaphore,
    )


async def get_cover_path(
    data_dir,
    gameid,
    game_name,
    force_update=False,
    sgdb_api_key=None,
    sgdb_game_name=None,
    appid=None,
    http_client: httpx.AsyncClient | None = None,
    request_semaphore=None,
):
    cover_dir = ensure_path(data_dir) / "covers_v"
    cover_dir.mkdir(parents=True, exist_ok=True)
    path = cover_dir / f"{gameid}.jpg"
    if path.exists() and not force_update:
        return str(path)

    cover_url = await get_sgdb_vertical_cover(
        game_name,
        sgdb_api_key=sgdb_api_key,
        sgdb_game_name=sgdb_game_name,
        appid=appid,
        http_client=http_client,
        request_semaphore=request_semaphore,
    )
    if not cover_url:
        return str(path) if path.exists() else None

    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            downloaded_path = await download_to_path(client, cover_url, str(path), timeout=10)
    else:
        downloaded_path = await download_to_path(
            http_client,
            cover_url,
            str(path),
            timeout=10,
            semaphore=request_semaphore,
        )
    if downloaded_path:
        return downloaded_path
    return str(path) if path.exists() else None