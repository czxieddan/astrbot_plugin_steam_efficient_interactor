import io
import logging
import os
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

from .runtime_utils import download_to_path, fetch_json

logger = logging.getLogger(__name__)

BG_COLOR_TOP = (49, 80, 66)
BG_COLOR_BOTTOM = (28, 35, 44)
AVATAR_SIZE = 80
COVER_W, COVER_H = 80, 120
IMG_W, IMG_H = 512, 192


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
    avatar_dir = os.path.join(data_dir, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    path = os.path.join(avatar_dir, f"{steamid}.jpg")
    refresh_interval = 24 * 3600
    if os.path.exists(path) and not force_update:
        if time.time() - os.path.getmtime(path) < refresh_interval:
            return path
    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            return await download_to_path(client, url, path, timeout=10)
    return await download_to_path(http_client, url, path, timeout=10, semaphore=request_semaphore)


async def get_sgdb_vertical_cover(
    game_name,
    sgdb_api_key=None,
    sgdb_game_name=None,
    appid=None,
    http_client: httpx.AsyncClient | None = None,
    request_semaphore=None,
):
    if not sgdb_api_key:
        return None
    headers = {"Authorization": f"Bearer {sgdb_api_key}"}
    search_name = sgdb_game_name if sgdb_game_name else game_name
    if not search_name:
        return None

    async def _get_cover_url(client: httpx.AsyncClient, candidate_name: str) -> str | None:
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
        first_item = grid_data["data"][0]
        return first_item.get("url")

    async def _resolve_from_appid(client: httpx.AsyncClient) -> str | None:
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
        sgdb_name = data["data"].get("name")
        if not sgdb_name:
            return None
        return await _get_cover_url(client, sgdb_name)

    async def _run(client: httpx.AsyncClient) -> str | None:
        cover_url = await _get_cover_url(client, search_name)
        if cover_url:
            return cover_url
        return await _resolve_from_appid(client)

    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            return await _run(client)
    return await _run(http_client)


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
    cover_dir = os.path.join(data_dir, "covers_v")
    os.makedirs(cover_dir, exist_ok=True)
    path = os.path.join(cover_dir, f"{gameid}.jpg")
    if os.path.exists(path) and not force_update:
        return path
    cover_url = await get_sgdb_vertical_cover(
        game_name,
        sgdb_api_key=sgdb_api_key,
        sgdb_game_name=sgdb_game_name,
        appid=appid,
        http_client=http_client,
        request_semaphore=request_semaphore,
    )
    if not cover_url:
        return path if os.path.exists(path) else None
    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            downloaded_path = await download_to_path(client, cover_url, path, timeout=10)
    else:
        downloaded_path = await download_to_path(
            http_client,
            cover_url,
            path,
            timeout=10,
            semaphore=request_semaphore,
        )
    if downloaded_path:
        return downloaded_path
    return path if os.path.exists(path) else None


def text_wrap(text, font, max_width):
    if not text:
        return [""]
    lines = []
    line = ""
    dummy_img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy_img)
    for char in text:
        bbox = draw.textbbox((0, 0), line + char, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            line += char
        else:
            if line:
                lines.append(line)
            line = char
    if line:
        lines.append(line)
    return lines


def get_chinese_length(text):
    length = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            length += 1
        else:
            length += 0.5
    return int(length + 0.5)


def pad_game_name(game_name, min_cn_len=10):
    current_length = get_chinese_length(game_name)
    pad_length = max(0, min_cn_len - current_length)
    return game_name + "　" * pad_length + "   "


def render_gradient_bg(img_w, img_h, color_top, color_bottom):
    image = Image.new("RGB", (img_w, img_h), color_top)
    top_r, top_g, top_b = color_top
    bottom_r, bottom_g, bottom_b = color_bottom
    for y in range(img_h):
        ratio = y / (img_h - 1)
        red = int(top_r * (1 - ratio) + bottom_r * ratio)
        green = int(top_g * (1 - ratio) + bottom_g * ratio)
        blue = int(top_b * (1 - ratio) + bottom_b * ratio)
        for x in range(img_w):
            image.putpixel((x, y), (red, green, blue))
    return image


async def get_playtime_hours(
    api_key,
    steamid,
    appid,
    retry_times=3,
    http_client: httpx.AsyncClient | None = None,
    request_semaphore=None,
):
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": api_key,
        "steamid": steamid,
        "include_appinfo": 0,
        "appids_filter[0]": appid,
    }

    async def _fetch_once(client: httpx.AsyncClient) -> float | None:
        status_code, data = await fetch_json(
            client,
            url,
            params=params,
            timeout=10,
            semaphore=request_semaphore,
        )
        if status_code != 200 or not data:
            return None
        games = data.get("response", {}).get("games", [])
        for game in games:
            if str(game.get("appid")) == str(appid):
                playtime_minutes = game.get("playtime_forever", 0)
                return round(playtime_minutes / 60, 1)
        return 0.0

    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            for attempt in range(max(1, retry_times)):
                playtime_hours = await _fetch_once(client)
                if playtime_hours is not None:
                    return playtime_hours
                if attempt < retry_times - 1:
                    await asyncio.sleep(1)
            return 0.0

    for attempt in range(max(1, retry_times)):
        playtime_hours = await _fetch_once(http_client)
        if playtime_hours is not None:
            return playtime_hours
        if attempt < retry_times - 1:
            await asyncio.sleep(1)
    return 0.0


def get_font_path(font_name):
    fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
    font_path = os.path.join(fonts_dir, font_name)
    if os.path.exists(font_path):
        return font_path
    fallback_path = os.path.join(os.path.dirname(__file__), font_name)
    if os.path.exists(fallback_path):
        return fallback_path
    return font_name


def render_game_start_image(
    player_name,
    avatar_path,
    game_name,
    cover_path,
    playtime_hours=None,
    superpower=None,
    online_count=None,
    font_path=None,
):
    fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
    font_regular = font_path or os.path.join(fonts_dir, "NotoSansHans-Regular.otf")
    font_medium = font_regular.replace("Regular", "Medium") if "Regular" in font_regular else os.path.join(fonts_dir, "NotoSansHans-Medium.otf")
    if not os.path.isabs(font_regular):
        font_regular = os.path.join(fonts_dir, os.path.basename(font_regular))
    if not os.path.isabs(font_medium):
        font_medium = os.path.join(fonts_dir, os.path.basename(font_medium))
    if not os.path.exists(font_regular):
        font_regular = os.path.join(fonts_dir, "NotoSansHans-Regular.otf")
    if not os.path.exists(font_medium):
        font_medium = os.path.join(fonts_dir, "NotoSansHans-Medium.otf")

    try:
        font_bold = ImageFont.truetype(font_medium, 28)
        font = ImageFont.truetype(font_regular, 22)
        font_small = ImageFont.truetype(font_regular, 16)
    except Exception:
        font_bold = font = font_small = ImageFont.load_default()

    image = render_gradient_bg(IMG_W, IMG_H, BG_COLOR_TOP, BG_COLOR_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    cover_width = COVER_W
    if cover_path and os.path.exists(cover_path):
        try:
            cover_source = Image.open(cover_path).convert("RGBA")
            scale = IMG_H / cover_source.height
            cover_width = int(cover_source.width * scale)
            cover_height = IMG_H
            cover_resized = cover_source.resize((cover_width, cover_height), Image.LANCZOS)
            image.paste(cover_resized, (0, 0), cover_resized)
        except Exception:
            cover_width = COVER_W

    avatar_margin = 24
    avatar_x = cover_width + avatar_margin
    text_x = avatar_x + AVATAR_SIZE + avatar_margin
    text_area_width = IMG_W - text_x - avatar_margin
    game_name_lines = text_wrap(pad_game_name(game_name, min_cn_len=10), font, text_area_width)
    line_height = 36
    block_height = line_height * (2 + len(game_name_lines)) + 10 + font_small.size + 4
    text_y = (IMG_H - block_height) // 2
    avatar_y = text_y + 10

    if avatar_path and os.path.exists(avatar_path):
        try:
            avatar = Image.open(avatar_path).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE))
            mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle((0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=AVATAR_SIZE // 5, fill=255)
            avatar.putalpha(mask)
            image.alpha_composite(avatar, (avatar_x, avatar_y))

        except Exception:
            pass

    online_text = None
    online_text_width = 0
    if online_count is not None:
        try:
            font_online = ImageFont.truetype(font_regular, 14)
        except Exception:
            font_online = ImageFont.load_default()
        online_text = f"●玩家人数{online_count}"
        bbox = draw.textbbox((0, 0), online_text, font=font_online)
        online_text_width = bbox[2] - bbox[0] + 10

    max_player_name_width = IMG_W - (text_x + 8) - online_text_width - 24
    player_font_size = 28
    for size in range(28, 15, -2):
        try:
            candidate_font = ImageFont.truetype(font_medium, size)
        except Exception:
            candidate_font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), player_name, font=candidate_font)
        if bbox[2] - bbox[0] <= max_player_name_width:
            player_font_size = size
            break
    try:
        player_font = ImageFont.truetype(font_medium, player_font_size)
    except Exception:
        player_font = ImageFont.load_default()

    draw.text((text_x + 8, text_y), player_name, font=player_font, fill=(255, 255, 255, 255))
    draw.text((text_x + 8, text_y + line_height), "正在玩", font=font, fill=(200, 255, 200, 255))
    for index, line in enumerate(game_name_lines):
        draw.text((text_x + 8, text_y + line_height * 2 + index * line_height), line, font=font, fill=(129, 173, 81, 255))

    if playtime_hours is not None:
        playtime_text = f"游戏时间 {playtime_hours} 小时"
        playtime_y = text_y + line_height * 2 + len(game_name_lines) * line_height + 4
        draw.text((text_x + 8, playtime_y), playtime_text, font=font_small, fill=(120, 180, 255, 255))

    if online_text:
        draw.text((IMG_W - online_text_width, 10), online_text, font=font_online, fill=(120, 180, 255, 180))

    return image.convert("RGB")


async def render_game_start(
    data_dir,
    steamid,
    player_name,
    avatar_url,
    gameid,
    game_name,
    api_key=None,
    superpower=None,
    online_count=None,
    sgdb_api_key=None,
    font_path=None,
    sgdb_game_name=None,
    appid=None,
    http_client: httpx.AsyncClient | None = None,
    request_semaphore=None,
):
    avatar_path = await get_avatar_path(
        data_dir,
        steamid,
        avatar_url,
        http_client=http_client,
        request_semaphore=request_semaphore,
    )
    cover_path = await get_cover_path(
        data_dir,
        gameid,
        game_name,
        sgdb_api_key=sgdb_api_key,
        sgdb_game_name=sgdb_game_name,
        appid=appid,
        http_client=http_client,
        request_semaphore=request_semaphore,
    )
    playtime_hours = None
    if api_key:
        playtime_hours = await get_playtime_hours(
            api_key,
            steamid,
            gameid,
            http_client=http_client,
            request_semaphore=request_semaphore,
        )
    image = render_game_start_image(
        player_name,
        avatar_path,
        game_name,
        cover_path,
        playtime_hours,
        superpower,
        online_count,
        font_path=font_path,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()