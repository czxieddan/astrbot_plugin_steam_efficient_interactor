import io
import logging
import os
from typing import Any, Dict, Optional, Set

import httpx
from PIL import Image, ImageDraw, ImageFont

from .runtime_utils import fetch_bytes, fetch_json, load_json_file, save_json_file

logger = logging.getLogger(__name__)


class AchievementMonitor:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.achievements_file = os.path.join(data_dir, "achievements_cache.json")
        self.initial_achievements = load_json_file(self.achievements_file, {})
        self.details_cache: dict[tuple[str, int], dict[str, Any]] = {}
        self.enable_failure_blacklist = False
        self.achievement_blacklist = set(load_json_file(self._blacklist_path(), []))

    def _blacklist_path(self):
        return os.path.join(self.data_dir, "achievement_blacklist.json")

    def _load_blacklist(self):
        self.achievement_blacklist = set(load_json_file(self._blacklist_path(), []))

    def _save_blacklist(self):
        save_json_file(self._blacklist_path(), list(self.achievement_blacklist))

    def _load_achievements_cache(self):
        self.initial_achievements = load_json_file(self.achievements_file, {})

    def _save_achievements_cache(self):
        save_json_file(self.achievements_file, self.initial_achievements)

    async def get_player_achievements(
        self,
        api_key: str,
        group_id: str,
        steamid: str,
        appid: int,
        http_client: httpx.AsyncClient | None = None,
        request_semaphore=None,
    ) -> Optional[Set[str]]:
        if hasattr(self, "achievement_blacklist") and str(appid) in self.achievement_blacklist:
            return None

        url = "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/"
        language_list = ["schinese", "english", "en"]
        params_base = {
            "key": api_key,
            "steamid": steamid,
            "appid": appid,
        }

        async def _fetch_once(client: httpx.AsyncClient, language: str) -> Optional[Set[str]]:
            params = dict(params_base)
            params["l"] = language
            status_code, data = await fetch_json(
                client,
                url,
                params=params,
                timeout=15,
                semaphore=request_semaphore,
            )
            if status_code == 403:
                logger.warning(f"获取成就数据被拒绝: steamid={steamid}, appid={appid}, lang={language}")
                return None
            if status_code == 401:
                return None
            if status_code != 200 or not data:
                return None
            achievements = data.get("playerstats", {}).get("achievements", [])
            if not achievements:
                return set()
            return {achievement["apiname"] for achievement in achievements if achievement.get("achieved", 0) == 1}

        async def _run(client: httpx.AsyncClient) -> Optional[Set[str]]:
            for language in language_list:
                result = await _fetch_once(client, language)
                if result is not None:
                    return result
            if self.enable_failure_blacklist:
                self.achievement_blacklist.add(str(appid))
                self._save_blacklist()
            return None

        if http_client is None:
            async with httpx.AsyncClient(timeout=15) as client:
                return await _run(client)
        return await _run(http_client)

    async def get_achievement_details(
        self,
        group_id: str,
        appid: int,
        lang: str = "schinese",
        api_key: str = "",
        steamid: str = "",
        http_client: httpx.AsyncClient | None = None,
        request_semaphore=None,
    ) -> Dict[str, Any]:
        if hasattr(self, "achievement_blacklist") and str(appid) in self.achievement_blacklist:
            return {}

        cache_key = (group_id, appid)
        cached = self.details_cache.get(cache_key)
        if cached:
            return cached

        language_list = [lang, "schinese", "english", "en"]
        stats_url = f"https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid={appid}"

        def to_icon_url(value: str | None) -> str | None:
            if not value:
                return None
            if value.startswith("http://") or value.startswith("https://"):
                return value
            return f"https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{value}.jpg"

        async def _load_from_schema(client: httpx.AsyncClient, language: str) -> dict[str, Any]:
            schema_url = f"https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/?appid={appid}&key={api_key}&l={language}"
            status_code, schema = await fetch_json(
                client,
                schema_url,
                timeout=15,
                semaphore=request_semaphore,
            )
            if status_code in (401, 403) or not schema:
                return {}
            if status_code == 400:
                return {}
            if status_code != 200:
                return {}

            achievements = {}
            game = schema.get("game", {})
            stats = game.get("availableGameStats", {})
            for achievement in stats.get("achievements", []):
                api_name = achievement["name"]
                achievements[api_name] = {
                    "name": achievement.get("displayName", api_name),
                    "description": achievement.get("description", ""),
                    "icon": to_icon_url(achievement.get("icon")),
                    "icon_gray": to_icon_url(achievement.get("icongray")),
                }
            return achievements

        async def _load_from_player_achievements(client: httpx.AsyncClient, language: str) -> dict[str, Any]:
            if not api_key or not steamid:
                return {}
            params = {
                "key": api_key,
                "steamid": steamid,
                "appid": appid,
                "l": language,
            }
            status_code, data = await fetch_json(
                client,
                "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/",
                params=params,
                timeout=15,
                semaphore=request_semaphore,
            )
            if status_code != 200 or not data:
                return {}
            achievements = {}
            for achievement in data.get("playerstats", {}).get("achievements", []):
                api_name = achievement.get("apiname")
                if not api_name:
                    continue
                achievements[api_name] = {
                    "name": achievement.get("name", api_name),
                    "description": achievement.get("description", ""),
                    "icon": None,
                    "icon_gray": None,
                }
            return achievements

        async def _load_percents(client: httpx.AsyncClient) -> dict[str, Any]:
            status_code, data = await fetch_json(
                client,
                stats_url,
                timeout=15,
                semaphore=request_semaphore,
            )
            if status_code != 200 or not data:
                return {}
            percents = {}
            for achievement in data.get("achievementpercentages", {}).get("achievements", []):
                name = achievement.get("name")
                if name:
                    percents[name] = achievement.get("percent")
            return percents

        async def _run(client: httpx.AsyncClient) -> dict[str, Any]:
            details = {}
            for language in language_list:
                achievements = await _load_from_schema(client, language)
                if not achievements:
                    achievements = await _load_from_player_achievements(client, language)
                if achievements:
                    percents = await _load_percents(client)
                    for api_name, achievement in achievements.items():
                        details[api_name] = {
                            "name": achievement.get("name", api_name),
                            "description": achievement.get("description", ""),
                            "icon": achievement.get("icon"),
                            "icon_gray": achievement.get("icon_gray"),
                            "percent": percents.get(api_name),
                        }
                    if any(item.get("description") for item in details.values()):
                        break
            self.details_cache[cache_key] = details
            return details

        if http_client is None:
            async with httpx.AsyncClient(timeout=15) as client:
                return await _run(client)
        return await _run(http_client)

    async def check_new_achievements(
        self,
        api_key: str,
        group_id: str,
        steamid: str,
        appid: int,
        player_name: str,
        game_name: str,
        http_client: httpx.AsyncClient | None = None,
        request_semaphore=None,
    ) -> Set[str]:
        cache_key = (group_id, steamid, appid)
        current_achievements = await self.get_player_achievements(
            api_key,
            group_id,
            steamid,
            appid,
            http_client=http_client,
            request_semaphore=request_semaphore,
        )
        if current_achievements is None:
            return set()
        initial_achievements = set(self.initial_achievements.get(str(cache_key), []))
        new_achievements = current_achievements - initial_achievements
        self.initial_achievements[str(cache_key)] = list(current_achievements)
        self._save_achievements_cache()
        return new_achievements

    def clear_game_achievements(self, group_id: str, steamid: str, appid: str):
        cache_key = (group_id, steamid, appid)
        if str(cache_key) in self.initial_achievements:
            del self.initial_achievements[str(cache_key)]
            self._save_achievements_cache()

    def render_achievement_message(self, achievement_details: dict, new_achievements: set, player_name: str = "") -> str:
        lines = []
        for api_name in new_achievements:
            detail = achievement_details.get(api_name)
            if not detail:
                continue
            icon_url = detail.get("icon")
            percent = detail.get("percent")
            try:
                percent_value = float(percent) if percent is not None else None
            except (ValueError, TypeError):
                percent_value = None
            percent_text = f"{percent_value:.1f}%" if percent_value is not None else "未知"
            name = detail.get("name", api_name)
            description = detail.get("description", "")
            lines.append(
                f"{player_name}解锁了成就\n"
                f"| ![{name}]({icon_url}) | <div align='left'>**{name}**<br>{description}<br>全球解锁率：{percent_text}</div> |\n"
                f"|:---:|:---|\n"
            )
        return "\n".join(lines)

    def _wrap_text(self, text, font, max_width):
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

    async def render_achievement_image(
        self,
        achievement_details: dict,
        new_achievements: set,
        player_name: str = "",
        steamid: str = None,
        appid: int = None,
        unlocked_set: set = None,
        font_path=None,
        http_client: httpx.AsyncClient | None = None,
        request_semaphore=None,
    ) -> bytes:
        width = 420
        padding_v = 18
        padding_h = 18
        card_gap = 14
        card_radius = 9
        card_inner_bg = (38, 44, 56, 220)
        card_base_bg = (35, 38, 46, 255)
        icon_size = 64
        icon_margin_right = 16
        text_margin_top = 10
        max_text_width = width - padding_h * 2 - icon_size - icon_margin_right - 18

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
            font_title = ImageFont.truetype(font_medium, 20)
            font_game = ImageFont.truetype(font_regular, 15)
            font_name = ImageFont.truetype(font_medium, 16)
            font_desc = ImageFont.truetype(font_regular, 13)
            font_percent = ImageFont.truetype(font_regular, 12)
            font_game_small = ImageFont.truetype(font_regular, 12)
            font_time = ImageFont.truetype(font_regular, 10)
        except Exception:
            font_title = font_game = font_name = font_desc = font_percent = font_game_small = font_time = ImageFont.load_default()

        unlocked_achievements = len(unlocked_set or set())
        total_achievements = len(achievement_details)
        progress_percent = int(unlocked_achievements / total_achievements * 100) if total_achievements else 0

        title_text = f"{player_name} 解锁新成就"
        game_name = ""
        for detail in achievement_details.values():
            if detail and detail.get("game_name"):
                game_name = detail["game_name"]
                break
        if not game_name:
            game_name = "未知游戏"

        now_text = time.strftime("%m-%d %H:%M")

        dummy_image = Image.new("RGB", (10, 10))
        dummy_draw = ImageDraw.Draw(dummy_image)
        title_bbox = dummy_draw.textbbox((0, 0), title_text, font=font_title)
        title_h = title_bbox[3] - title_bbox[1]
        game_bbox = dummy_draw.textbbox((0, 0), game_name, font=font_game_small)
        game_h = game_bbox[3] - game_bbox[1]
        progress_bar_h = 12
        progress_bar_margin = 8
        title_game_gap = 8
        header_h = title_h + title_game_gap + game_h + progress_bar_h + progress_bar_margin * 3

        card_heights = []
        card_texts = []
        percent_values = []
        for api_name in new_achievements:
            detail = achievement_details.get(api_name)
            if not detail:
                card_heights.append(80)
                card_texts.append(([""], [""], "未知"))
                percent_values.append(0)
                continue
            name = detail.get("name", api_name)
            description = detail.get("description", "")
            percent = detail.get("percent")
            try:
                percent_value = float(percent) if percent is not None else None
            except (ValueError, TypeError):
                percent_value = None
            percent_text = f"{percent_value:.1f}%" if percent_value is not None else "未知"
            name_lines = self._wrap_text(name, font_name, max_text_width)
            desc_lines = self._wrap_text(description, font_desc, max_text_width)
            card_height = max(icon_size + 24, len(name_lines) * 22 + len(desc_lines) * 18 + 60)
            card_heights.append(card_height)
            card_texts.append((name_lines, desc_lines, percent_text))
            percent_values.append(percent_value if percent_value is not None else 0)

        total_height = padding_v + header_h + padding_v + sum(card_heights) + card_gap * (len(card_heights) - 1) + padding_v
        image = Image.new("RGBA", (width, total_height), (20, 26, 33, 255))
        draw = ImageDraw.Draw(image)

        draw.text((padding_h, padding_v), title_text, fill=(255, 255, 255), font=font_title)
        draw.text((padding_h, padding_v + title_h + title_game_gap), game_name, fill=(160, 160, 160), font=font_game_small)
        time_bbox = draw.textbbox((0, 0), now_text, font=font_time)
        draw.text((width - padding_h - (time_bbox[2] - time_bbox[0]), padding_v), now_text, fill=(168, 168, 168), font=font_time)

        bar_x = padding_h
        bar_y = padding_v + title_h + title_game_gap + game_h + progress_bar_margin
        bar_w = width - padding_h * 2
        bar_h = progress_bar_h
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=bar_h // 2, fill=(60, 62, 70, 180))
        fill_w = int(bar_w * progress_percent / 100)
        if fill_w > 0:
            draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=bar_h // 2, fill=(26, 159, 255, 255))
        progress_text = f"{unlocked_achievements}/{total_achievements} ({progress_percent}%)"
        progress_bbox = draw.textbbox((0, 0), progress_text, font=font_percent)
        draw.text((bar_x + bar_w - (progress_bbox[2] - progress_bbox[0]) - 6, bar_y - 2), progress_text, fill=(142, 207, 255), font=font_percent)

        async def _icon_bytes(client: httpx.AsyncClient | None, icon_url: str | None):
            if not icon_url:
                return None
            if client is None:
                async with httpx.AsyncClient(timeout=10) as local_client:
                    return await fetch_bytes(local_client, icon_url, timeout=10)
            return await fetch_bytes(client, icon_url, timeout=10, semaphore=request_semaphore)

        y = padding_v + header_h + padding_v
        if http_client is None:
            async with httpx.AsyncClient(timeout=10) as local_client:
                active_client = local_client
                for idx, api_name in enumerate(new_achievements):
                    detail = achievement_details.get(api_name)
                    if not detail:
                        y += card_heights[idx] + card_gap
                        continue
                    icon_data = await _icon_bytes(active_client, detail.get("icon"))
                    y = self._draw_card(
                        image,
                        draw,
                        detail,
                        icon_data,
                        card_heights[idx],
                        card_texts[idx],
                        percent_values[idx],
                        padding_h,
                        y,
                        width,
                        icon_size,
                        icon_margin_right,
                        text_margin_top,
                        card_radius,
                        card_base_bg,
                        card_inner_bg,
                        font_name,
                        font_desc,
                        font_percent,
                    )
                    y += card_gap
        else:
            for idx, api_name in enumerate(new_achievements):
                detail = achievement_details.get(api_name)
                if not detail:
                    y += card_heights[idx] + card_gap
                    continue
                icon_data = await _icon_bytes(http_client, detail.get("icon"))
                y = self._draw_card(
                    image,
                    draw,
                    detail,
                    icon_data,
                    card_heights[idx],
                    card_texts[idx],
                    percent_values[idx],
                    padding_h,
                    y,
                    width,
                    icon_size,
                    icon_margin_right,
                    text_margin_top,
                    card_radius,
                    card_base_bg,
                    card_inner_bg,
                    font_name,
                    font_desc,
                    font_percent,
                )
                y += card_gap

        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG")
        return output.getvalue()

    def _draw_card(
        self,
        image,
        draw,
        detail,
        icon_data,
        card_height,
        card_text,
        percent_value,
        padding_h,
        y,
        width,
        icon_size,
        icon_margin_right,
        text_margin_top,
        card_radius,
        card_base_bg,
        card_inner_bg,
        font_name,
        font_desc,
        font_percent,
    ):
        name_lines, desc_lines, percent_text = card_text
        card_x0 = padding_h
        card_x1 = width - padding_h
        card_y0 = int(y)
        card_y1 = int(y + card_height)
        card_w = card_x1 - card_x0
        card_h = card_y1 - card_y0

        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        mask = Image.new("L", (card_w, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_w, card_h), radius=card_radius, fill=255)
        card_bg = Image.new("RGBA", (card_w, card_h), card_base_bg)
        card.paste(card_bg, (0, 0), mask)

        if percent_value < 10:
            border_draw = ImageDraw.Draw(card)
            border_rect = (1, 1, card_w - 2, card_h - 2)
            border_draw.rounded_rectangle(border_rect, radius=card_radius, outline=(255, 215, 128, 255), width=3)

        bar_margin_x = 18
        bar_margin_y = 12
        bar_height = 8
        bar_x0 = bar_margin_x
        bar_x1 = card_w - bar_margin_x
        bar_y1 = card_h - bar_margin_y
        bar_y0 = bar_y1 - bar_height
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y1), radius=bar_height // 2, fill=(60, 62, 70, 180))
        if percent_value > 0:
            fill_w = int((bar_x1 - bar_x0) * percent_value / 100)
            if fill_w > 0:
                card_draw.rounded_rectangle((bar_x0, bar_y0, bar_x0 + fill_w, bar_y1), radius=bar_height // 2, fill=(26, 159, 255, 255))

        card_fg = Image.new("RGBA", (card_w, card_h), card_inner_bg)
        card.paste(card_fg, (0, 0), mask)
        image.alpha_composite(card, (card_x0, card_y0))

        icon_image = None
        if icon_data:
            try:
                icon_image = Image.open(io.BytesIO(icon_data)).convert("RGBA")
                icon_image = icon_image.resize((icon_size, icon_size), Image.LANCZOS)
                icon_mask = Image.new("L", (icon_size, icon_size), 0)
                ImageDraw.Draw(icon_mask).rounded_rectangle((0, 0, icon_size, icon_size), 12, fill=255)
                icon_image.putalpha(icon_mask)
            except Exception:
                icon_image = None

        icon_x = card_x0 + 12
        icon_y = card_y0 + (card_height - icon_size) // 2
        if icon_image:
            if percent_value < 10:
                glow_size = 10
                canvas_size = icon_size + 2 * glow_size
                icon_canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
                glow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
                glow_draw = ImageDraw.Draw(glow)
                for radius in range(canvas_size // 2, icon_size // 2, -1):
                    alpha = int(120 * (canvas_size // 2 - radius) / glow_size)
                    glow_draw.ellipse(
                        [
                            canvas_size // 2 - radius,
                            canvas_size // 2 - radius,
                            canvas_size // 2 + radius,
                            canvas_size // 2 + radius,
                        ],
                        fill=(255, 220, 60, max(0, alpha)),
                    )
                icon_canvas = Image.alpha_composite(icon_canvas, glow)
                icon_canvas.paste(icon_image, (glow_size, glow_size), icon_image)
                image.alpha_composite(icon_canvas, (icon_x - glow_size, icon_y - glow_size))
            else:
                image.alpha_composite(icon_image, (icon_x, icon_y))

        text_x = icon_x + icon_size + icon_margin_right
        text_y = card_y0 + text_margin_top
        for index, line in enumerate(name_lines):
            draw.text((text_x, text_y + index * 22), line, fill=(255, 255, 255), font=font_name)
        desc_y = text_y + len(name_lines) * 22 + 2
        for index, line in enumerate(desc_lines):
            draw.text((text_x, desc_y + index * 18), line, fill=(187, 187, 187), font=font_desc)

        percent_y = desc_y + len(desc_lines) * 18 + 6
        percent_label = "全球解锁率："
        label_bbox = draw.textbbox((0, 0), percent_label, font=font_percent)
        label_w = label_bbox[2] - label_bbox[0]
        text_color = (142, 207, 255) if percent_value >= 10 else (255, 220, 60)
        draw.text((text_x, percent_y), percent_label, fill=text_color, font=font_percent)
        percent_value_bbox = draw.textbbox((0, 0), percent_text, font=font_percent)
        value_x = text_x + label_w + 4 + (card_x1 - (text_x + label_w + 4) - 48)
        draw.text((value_x, percent_y), percent_text, fill=text_color, font=font_percent)

        return card_y1