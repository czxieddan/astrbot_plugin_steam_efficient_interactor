import io
import os
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger

from .render_common import get_avatar_path, get_cover_path, render_gradient_bg

BG_COLOR_TOP = (24, 18, 48)
BG_COLOR_BOTTOM = (8, 8, 16)
AVATAR_SIZE = 80
COVER_W, COVER_H = 80, 120
IMG_W, IMG_H = 512, 192
STAR_BG_PATH = os.path.join(os.path.dirname(__file__), "随机散布的小星星767xx809xp.png")


def draw_duration_bar(draw, x, y, width, height, duration_h):
    pad = 1
    draw.rounded_rectangle(
        [x - pad, y - pad, x + width + pad, y + height + pad],
        radius=(height + pad) // 2,
        fill=(0, 0, 0, 180),
    )
    draw.rounded_rectangle([x, y, x + width, y + height], radius=height // 2, outline=(0, 0, 0, 255), width=1)
    draw.rounded_rectangle(
        [x - 2, y - 2, x + width + 2, y + height + 2],
        radius=(height + 4) // 2,
        outline=(255, 255, 255, 220),
        width=1,
    )

    bar_colors = [
        (80, 200, 120),
        (255, 220, 80),
        (255, 160, 80),
        (255, 80, 80),
        (200, 80, 160),
        (120, 80, 200),
    ]
    segment_limits = [1, 3, 5, 7, 9, 12]
    segment_starts = [0] + segment_limits[:-1]
    segment_texts = [None, "2X", "3X", "4X", "5X", "6X"]

    if duration_h > 12:
        for index in range(width):
            ratio = index / max(width - 1, 1)
            red = int(255 * ratio)
            green = int(200 * (1 - ratio / 2))
            blue = int(255 * (1 - ratio))
            draw.line([(x + index, y), (x + index, y + height)], fill=(red, green, blue), width=1)
        try:
            font = ImageFont.truetype("msyhbd.ttc", height + 8)
        except Exception:
            font = ImageFont.load_default()
        text = "MAX"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_x = x + width // 2 - (bbox[2] - bbox[0]) // 2
        text_y = y + height // 2 - (bbox[3] - bbox[1]) // 2 - 5
        draw.text(
            (text_x, text_y),
            text,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 180),
        )
        return

    for segment_start, segment_end, color in zip(segment_starts, segment_limits, bar_colors):
        segment_value = min(max(duration_h - segment_start, 0), segment_end - segment_start)
        segment_ratio = segment_value / (segment_end - segment_start) if segment_end > segment_start else 0
        segment_width = int(width * segment_ratio)
        if segment_width > 0:
            draw.rounded_rectangle([x, y, x + segment_width, y + height], radius=height // 2, fill=color)

    for index, (segment_start, _, color) in enumerate(zip(segment_starts, segment_limits, bar_colors)):
        text = segment_texts[index]
        if text and duration_h > segment_start:
            try:
                font = ImageFont.truetype("msyhbd.ttc", height + 6)
            except Exception:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=font)
            text_x = x + width // 2 - (bbox[2] - bbox[0]) // 2
            text_y = y + height // 2 - (bbox[3] - bbox[1]) // 2 - 5
            draw.text((text_x, text_y), text, font=font, fill=color, stroke_width=2, stroke_fill=(0, 0, 0, 180))


def text_wrap(text, font, max_width):
    if not text:
        return [""]
    lines = []
    line = ""
    dummy_image = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy_image)
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


def render_game_end_image(player_name, avatar_path, game_name, cover_path, end_time_str, tip_text, duration_h, font_path=None):
    fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
    font_regular = font_path or os.path.join(fonts_dir, "NotoSansHans-Regular.otf")
    font_medium = (
        font_regular.replace("Regular", "Medium")
        if "Regular" in font_regular
        else os.path.join(fonts_dir, "NotoSansHans-Medium.otf")
    )
    if not os.path.isabs(font_regular):
        font_regular = os.path.join(fonts_dir, os.path.basename(font_regular))
    if not os.path.isabs(font_medium):
        font_medium = os.path.join(fonts_dir, os.path.basename(font_medium))
    if not os.path.exists(font_regular):
        font_regular = os.path.join(fonts_dir, "NotoSansHans-Regular.otf")
    if not os.path.exists(font_medium):
        font_medium = os.path.join(fonts_dir, "NotoSansHans-Medium.otf")

    try:
        font_title = ImageFont.truetype(font_medium, 28)
        font_game = ImageFont.truetype(font_regular, 22)
        font_tip = ImageFont.truetype(font_regular, 16)
        font_luck = ImageFont.truetype(font_regular, 14)
        font_time = ImageFont.truetype(font_regular, 8)
    except Exception:
        font_title = font_game = font_tip = font_luck = font_time = ImageFont.load_default()

    image = render_gradient_bg(IMG_W, IMG_H, BG_COLOR_TOP, BG_COLOR_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    if os.path.exists(STAR_BG_PATH):
        try:
            star_image = Image.open(STAR_BG_PATH).convert("RGBA")
            scale = IMG_H / star_image.height
            resized_width = int(star_image.width * scale)
            resized_height = IMG_H
            resized_star = star_image.resize((resized_width, resized_height), Image.LANCZOS)
            alpha = resized_star.split()[-1].point(lambda value: int(value * 0.3))
            resized_star.putalpha(alpha)
            for x in range(0, IMG_W, resized_width):
                image.alpha_composite(resized_star, (x, 0))
        except Exception as error:
            logger.warning(f"结束游戏星空背景渲染失败: {error}")

    cover_width = COVER_W
    if cover_path and os.path.exists(cover_path):
        try:
            cover_source = Image.open(cover_path).convert("RGBA")
            scale = IMG_H / cover_source.height
            cover_width = int(cover_source.width * scale)
            cover_height = IMG_H
            cover_resized = cover_source.resize((cover_width, cover_height), Image.LANCZOS)
            if cover_width > IMG_W:
                cover_resized = cover_resized.crop((0, 0, IMG_W, cover_height))
                cover_width = IMG_W
            image.paste(cover_resized, (0, 0), cover_resized)
        except Exception as error:
            logger.warning(f"结束游戏封面渲染失败: {error}")
            cover_width = COVER_W

    avatar_x = cover_width + 24
    avatar_y = 16
    if avatar_path and os.path.exists(avatar_path):
        try:
            avatar = Image.open(avatar_path).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE))
            mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.rounded_rectangle((0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=AVATAR_SIZE // 5, fill=255)
            avatar.putalpha(mask)
            image.alpha_composite(avatar, (avatar_x, avatar_y))
        except Exception as error:
            logger.warning(f"结束游戏头像渲染失败: {error}")

    try:
        time_text = time.strftime("%H:%M", time.strptime(end_time_str, "%Y-%m-%d %H:%M"))
    except Exception:
        time_text = end_time_str[-5:]

    bbox = draw.textbbox((0, 0), time_text, font=font_time, stroke_width=2)
    time_x = IMG_W - bbox[2] + bbox[0] - 18
    draw.text(
        (time_x, 6),
        time_text,
        font=font_time,
        fill=(255, 255, 255, 220),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255),
    )

    title_text = player_name
    max_title_width = IMG_W - (avatar_x + AVATAR_SIZE + 20) - 24
    title_font_size = 28
    for size in range(28, 15, -2):
        try:
            candidate_font = ImageFont.truetype(font_medium, size)
        except Exception:
            candidate_font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), title_text, font=candidate_font)
        if bbox[2] - bbox[0] <= max_title_width:
            title_font_size = size
            break
    try:
        title_font = ImageFont.truetype(font_medium, title_font_size)
    except Exception:
        title_font = ImageFont.load_default()

    draw.text(
        (avatar_x + AVATAR_SIZE + 20, 16),
        title_text,
        font=title_font,
        fill=(180, 160, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255),
    )

    game_name_y = 16 + title_font.size + 8
    max_game_name_width = IMG_W - (avatar_x + AVATAR_SIZE + 20) - 24
    game_name_lines = text_wrap(game_name, font_game, max_game_name_width)
    for index, line in enumerate(game_name_lines[:2]):
        draw.text(
            (avatar_x + AVATAR_SIZE + 20, game_name_y + index * (font_game.size + 2)),
            line,
            font=font_game,
            fill=(220, 220, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255),
        )

    bar_x = avatar_x
    bar_y = IMG_H - 24
    if duration_h < 1:
        duration_text = f"已玩{int(duration_h * 60)}分钟："
    else:
        duration_text = f"已玩{duration_h:.1f}小时："
    draw.text(
        (bar_x, bar_y - 2),
        duration_text,
        font=font_tip,
        fill=(180, 220, 255, 220),
        stroke_width=1,
        stroke_fill=(0, 0, 0, 255),
    )
    duration_bbox = draw.textbbox((bar_x, bar_y - 2), duration_text, font=font_tip)
    bar_start_x = duration_bbox[2] + 6
    bar_width = IMG_W - bar_start_x - 18
    if bar_width > 0:
        draw_duration_bar(draw, bar_start_x, bar_y + 6, bar_width, 6, duration_h)

    return image.convert("RGB")


async def render_game_end(
    data_dir,
    steamid,
    player_name,
    avatar_url,
    gameid,
    game_name,
    end_time_str,
    tip_text,
    duration_h,
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
    image = render_game_end_image(
        player_name,
        avatar_path,
        game_name,
        cover_path,
        end_time_str,
        tip_text,
        duration_h,
        font_path=font_path,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()