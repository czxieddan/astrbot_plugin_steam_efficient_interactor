import asyncio
import io
import os

import httpx
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger

from .runtime_utils import download_to_path

STEAM_BG_TOP = (44, 62, 80)
STEAM_BG_BOTTOM = (24, 32, 44)
CARD_BG = (38, 44, 56, 230)
CARD_RADIUS = 12
AVATAR_SIZE = 72
AVATAR_RADIUS = 12
CARD_HEIGHT = 110
CARD_MARGIN = 18
CARD_GAP = 12


async def fetch_avatar(avatar_url, data_dir, sid, http_client: httpx.AsyncClient | None = None, request_semaphore=None):
    if not avatar_url:
        return None
    avatar_dir = os.path.join(data_dir, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    path = os.path.join(avatar_dir, f"{sid}.jpg")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    if http_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            downloaded_path = await download_to_path(client, avatar_url, path, timeout=10)
    else:
        downloaded_path = await download_to_path(
            http_client,
            avatar_url,
            path,
            timeout=10,
            semaphore=request_semaphore,
        )
    if not downloaded_path:
        return None
    try:
        return Image.open(downloaded_path).convert("RGBA")
    except Exception:
        return None


def get_status_color(status):
    if status == "playing":
        return (80, 220, 120)
    if status == "online":
        return (80, 180, 255)
    if status == "away":
        return (255, 200, 80)
    if status == "snooze":
        return (180, 180, 180)
    if status == "busy":
        return (255, 100, 100)
    if status == "offline":
        return (255, 255, 255)
    return (180, 80, 80)


def get_name_color(status):
    if status == "playing":
        return (227, 255, 194)
    if status == "online":
        return (80, 180, 255)
    if status == "away":
        return (255, 200, 80)
    if status == "snooze":
        return (180, 180, 180)
    if status == "busy":
        return (255, 100, 100)
    if status == "offline":
        return (220, 220, 220)
    return (255, 120, 120)


def get_status_text(status):
    if status == "playing":
        return "正在游戏"
    if status == "online":
        return "在线"
    if status == "away":
        return "离开"
    if status == "snooze":
        return "打盹"
    if status == "busy":
        return "忙碌"
    if status == "offline":
        return "离线"
    return "异常"


def get_font_path(font_name):
    fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
    font_path = os.path.join(fonts_dir, font_name)
    if os.path.exists(font_path):
        return font_path
    fallback_path = os.path.join(os.path.dirname(__file__), font_name)
    if os.path.exists(fallback_path):
        return fallback_path
    return font_name


async def render_steam_list_image(data_dir, user_list, font_path=None, http_client: httpx.AsyncClient | None = None, request_semaphore=None):
    if font_path is None:
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "NotoSansHans-Regular.otf")
    try:
        font_title = ImageFont.truetype(font_path, 28)
        font_name = ImageFont.truetype(font_path, 22)
        font_game = ImageFont.truetype(font_path, 18)
        font_bold_path = font_path.replace("Regular", "Medium")
        if os.path.exists(font_bold_path):
            font_status = ImageFont.truetype(font_bold_path, 16)
        else:
            font_status = ImageFont.truetype(font_path, 16)
        font_small = ImageFont.truetype(font_path, 14)
    except Exception as error:
        logger.warning(f"Steam列表字体加载失败: {error}")
        font_title = font_name = font_game = font_status = font_small = ImageFont.load_default()

    user_count = len(user_list)
    width = 600
    height = CARD_MARGIN + user_count * (CARD_HEIGHT + CARD_GAP) + CARD_MARGIN + 50
    image = Image.new("RGBA", (width, height), STEAM_BG_TOP)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / (height - 1)
        red = int(STEAM_BG_TOP[0] * (1 - ratio) + STEAM_BG_BOTTOM[0] * ratio)
        green = int(STEAM_BG_TOP[1] * (1 - ratio) + STEAM_BG_BOTTOM[1] * ratio)
        blue = int(STEAM_BG_TOP[2] * (1 - ratio) + STEAM_BG_BOTTOM[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(red, green, blue))

    title = "Steam 玩家状态列表"
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    draw.text(((width - title_bbox[2] + title_bbox[0]) // 2, 12), title, font=font_title, fill=(255, 255, 255))

    avatar_tasks = [
        fetch_avatar(user["avatar_url"], data_dir, user["sid"], http_client=http_client, request_semaphore=request_semaphore)
        for user in user_list
    ]
    avatars = await asyncio.gather(*avatar_tasks)

    for index, user in enumerate(user_list):
        top = CARD_MARGIN + index * (CARD_HEIGHT + CARD_GAP) + 50
        left = CARD_MARGIN

        card = Image.new("RGBA", (width - 2 * CARD_MARGIN, CARD_HEIGHT), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle((0, 0, width - 2 * CARD_MARGIN, CARD_HEIGHT), radius=CARD_RADIUS, fill=CARD_BG)

        avatar = avatars[index]
        if avatar:
            avatar = avatar.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
            mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=AVATAR_RADIUS, fill=255)
            card.paste(avatar, (18, (CARD_HEIGHT - AVATAR_SIZE) // 2), mask)

        name_x = 18 + AVATAR_SIZE + 18
        name_y = 18
        name_color = (227, 255, 194) if user["status"] == "playing" else get_name_color(user["status"])
        card_draw.text((name_x, name_y), user["name"], font=font_name, fill=name_color)

        status_y = name_y + 28
        if user["status"] == "playing":
            card_draw.text((name_x, status_y), f"正在玩：{user['game']}", font=font_game, fill=(131, 175, 80))
            info_y = status_y + 26
            card_draw.text((name_x, info_y), f"时长：{user['play_str']}", font=font_small, fill=(180, 220, 180))
        elif user["status"] in ("online", "away", "snooze", "busy"):
            card_draw.text((name_x, status_y), get_status_text(user["status"]), font=font_status, fill=get_status_color(user["status"]))
        elif user["status"] == "offline":
            card_draw.text((name_x, status_y), "离线", font=font_game, fill=(255, 255, 255))
            if user["play_str"]:
                info_y = status_y + 26
                card_draw.text((name_x, info_y), user["play_str"], font=font_small, fill=(180, 180, 180))
        elif user["status"] == "error":
            card_draw.text((name_x, status_y), "异常", font=font_game, fill=(255, 120, 120))
            info_y = status_y + 26
            card_draw.text((name_x, info_y), user["play_str"], font=font_small, fill=(255, 120, 120))

        image.alpha_composite(card, (left, top))

    stat_text = f"在线: {sum(1 for user in user_list if user['status'] in ('online', 'playing'))} / 总数: {len(user_list)}"
    draw.text((width - 220, height - 36), stat_text, font=font_small, fill=(180, 220, 255))

    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG")
    return output.getvalue()