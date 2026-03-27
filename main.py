from collections import deque
from datetime import datetime
from pathlib import Path

import asyncio
import httpx
import io
import json
import os
import re
import shutil
import tempfile
import time
import traceback
from PIL import Image as PILImage, ImageChops
from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register

from .achievement_monitor import AchievementMonitor
from .game_end_render import render_game_end
from .game_start_render import render_game_start
from .openbox import handle_openbox
from .runtime_utils import (
    LogThrottle,
    create_temp_png,
    fetch_json,
    load_json_file,
    normalize_api_keys,
    save_json_file,
    safe_remove,
)
from .steam_api_service import SteamApiService
from .steam_config_service import SteamConfigService
from .steam_id_service import SteamIdService
from .steam_list import handle_steam_list
from .steam_monitor_service import SteamMonitorService
from .steam_notification_service import SteamNotificationService
from .superpower_util import get_daily_superpower, load_abilities


@register(
    "astrbot_plugin_steam_efficient_interactor",
    "Yezi & CzXieDdan",
    "Steam高效互动者",
    "2.8.2",
    "https://github.com/czxieddan/astrbot_plugin_steam_efficient_interactor",
)
class SteamStatusMonitorV2(Star):
    def _get_group_data_path(self, group_id, key):
        return self.data_dir / f"group_{group_id}_{key}.json"

    def _get_groups_file_path(self):
        return self.data_dir / "steam_groups.json"

    def _load_group_steam_ids(self):
        self.group_steam_ids = load_json_file(self._get_groups_file_path(), {})

    def _save_group_steam_ids(self):
        save_json_file(self._get_groups_file_path(), self.group_steam_ids)

    def _load_notify_session(self):
        self.notify_sessions = load_json_file(self.data_dir / "notify_sessions.json", {})

    def _save_notify_session(self):
        save_json_file(self.data_dir / "notify_sessions.json", self.notify_sessions)

    def _load_persistent_data(self):
        for group_id in self.group_steam_ids:
            self.group_last_states[group_id] = load_json_file(self._get_group_data_path(group_id, "states"), {})
            self.group_start_play_times[group_id] = load_json_file(self._get_group_data_path(group_id, "start_play_times"), {})
            self.group_last_quit_times[group_id] = load_json_file(self._get_group_data_path(group_id, "last_quit_times"), {})
            self.group_pending_logs[group_id] = load_json_file(self._get_group_data_path(group_id, "pending_logs"), {})
            self.group_pending_quit[group_id] = load_json_file(self._get_group_data_path(group_id, "pending_quit"), {})
            self.group_recent_games[group_id] = load_json_file(self._get_group_data_path(group_id, "recent_games"), [])

    def _save_persistent_data(self):
        for group_id in self.group_steam_ids:
            save_json_file(self._get_group_data_path(group_id, "states"), self.group_last_states.get(group_id, {}))
            save_json_file(self._get_group_data_path(group_id, "start_play_times"), self.group_start_play_times.get(group_id, {}))
            save_json_file(self._get_group_data_path(group_id, "last_quit_times"), self.group_last_quit_times.get(group_id, {}))
            save_json_file(self._get_group_data_path(group_id, "pending_logs"), self.group_pending_logs.get(group_id, {}))
            save_json_file(self._get_group_data_path(group_id, "pending_quit"), self.group_pending_quit.get(group_id, {}))
            save_json_file(self._get_group_data_path(group_id, "recent_games"), self.group_recent_games.get(group_id, []))

    def _mark_dirty(self):
        self._dirty = True

    def _record_event(self, group_id: str, steamid: str, player_name: str, event_type: str, message: str, **extra):
        self.recent_events.append(
            {
                "timestamp": int(time.time()),
                "group_id": str(group_id),
                "steamid": str(steamid),
                "player_name": str(player_name),
                "event_type": str(event_type),
                "message": str(message),
                "extra": extra,
            }
        )

    async def _periodic_save_loop(self):
        try:
            while True:
                await asyncio.sleep(60)
                if self._dirty:
                    self._save_persistent_data()
                    self._dirty = False
        except asyncio.CancelledError:
            if self._dirty:
                self._save_persistent_data()
            raise

    def _ensure_fonts(self):
        plugin_fonts_dir = Path(__file__).resolve().parent / "fonts"
        cache_fonts_dir = StarTools.get_data_dir("steam_status_monitor")
        plugin_fonts_dir.mkdir(parents=True, exist_ok=True)
        cache_fonts_dir.mkdir(parents=True, exist_ok=True)
        font_candidates = ["NotoSansHans-Regular.otf", "NotoSansHans-Medium.otf"]
        self.font_paths = {}
        for font_name in font_candidates:
            plugin_font_path = plugin_fonts_dir / font_name
            cache_font_path = cache_fonts_dir / font_name
            if plugin_font_path.exists():
                shutil.copy(plugin_font_path, cache_font_path)
                self.font_paths[font_name] = str(cache_font_path)
            elif cache_font_path.exists():
                self.font_paths[font_name] = str(cache_font_path)
            else:
                self.font_paths[font_name] = None
        if not all(self.font_paths.values()):
            logger.warning("未检测到全部 NotoSansHans 字体，渲染可能出现乱码。")

    def get_font_path(self, font_name=None, bold=False):
        if not font_name:
            font_name = "NotoSansHans-Regular.otf"
        if bold:
            font_name = "NotoSansHans-Medium.otf"
        return self.font_paths.get(font_name) or font_name

    def _get_next_api_key(self):
        if not self.steam_api_keys:
            return None
        self._current_api_key_index = (self._current_api_key_index + 1) % len(self.steam_api_keys)
        return self.steam_api_keys[self._current_api_key_index]

    def _process_steam_group_mapping(self, mapping_list):
        for mapping in mapping_list:
            if "|" not in mapping:
                self.log_throttle.log(f"invalid_mapping:{mapping}", logger.warning, f"无效的映射配置格式: {mapping}")
                continue
            try:
                steam_id, group_key = mapping.split("|", 1)
                steam_id = steam_id.strip()
                group_key = group_key.strip()
                if not steam_id.isdigit() or len(steam_id) != 17:
                    self.log_throttle.log(f"invalid_steamid:{steam_id}", logger.warning, f"无效的 SteamID: {steam_id}")
                    continue
                unified_session = None
                group_id = group_key
                if ":" in group_key:
                    unified_session = group_key
                    group_id_raw = group_key.split(":")[-1]
                    group_id = group_id_raw.split("_")[-1] if "_" in group_id_raw else group_id_raw
                self.group_steam_ids.setdefault(group_id, [])
                if steam_id not in self.group_steam_ids[group_id]:
                    self.group_steam_ids[group_id].append(steam_id)
                if unified_session:
                    self.notify_sessions.setdefault(group_id, unified_session)
            except Exception as error:
                self.log_throttle.log(f"mapping_error:{mapping}", logger.warning, f"处理映射配置失败: {error}")
        self._save_group_steam_ids()
        self._save_notify_session()

    def _log_api_key_status(self, stage: str, incoming_config: dict | None = None):
        runtime_config = self.config_service.load_runtime_config()
        incoming_keys = normalize_api_keys((incoming_config or {}).get("steam_api_key", []))
        runtime_keys = normalize_api_keys(runtime_config.get("steam_api_key", []))
        active_keys = list(self.steam_api_keys)
        active_index = self._current_api_key_index if active_keys else -1
        current_key = active_keys[active_index] if active_keys and 0 <= active_index < len(active_keys) else ""

        logger.info(
            "[steam_status_monitor] API配置诊断[%s] incoming=%s(%s) runtime=%s(%s) active=%s(%s) current_index=%s current_key=%s"
            % (
                stage,
                len(incoming_keys),
                self.config_service.mask_secret(incoming_keys),
                len(runtime_keys),
                self.config_service.mask_secret(runtime_keys),
                len(active_keys),
                self.config_service.mask_secret(active_keys),
                active_index,
                self.config_service.mask_secret(current_key),
            )
        )

    def _sync_runtime_from_config(self):
        defaults = {
            "steam_api_key": [],
            "sgdb_api_key": "",
            "fixed_poll_interval": 0,
            "retry_times": 2,
            "detailed_poll_log": False,
            "steam_group_mapping": [],
            "enable_failure_blacklist": False,
            "max_achievement_notifications": 5,
        }
        for key, value in defaults.items():
            self.config.setdefault(key, value)

        new_api_keys = normalize_api_keys(self.config.get("steam_api_key", []))
        old_api_keys = normalize_api_keys(self.config.get("steam_api_keys", []))
        self.steam_api_keys = new_api_keys if new_api_keys else old_api_keys
        self._current_api_key_index = 0
        self.API_KEY = self.steam_api_keys[0] if self.steam_api_keys else ""
        self.RETRY_TIMES = max(1, int(self.config.get("retry_times", 2)))
        self.max_group_size = 20
        self.fixed_poll_interval = max(0, int(self.config.get("fixed_poll_interval", 0)))
        self.detailed_poll_log = bool(self.config.get("detailed_poll_log", False))
        self.enable_failure_blacklist = bool(self.config.get("enable_failure_blacklist", False))
        self.max_achievement_notifications = max(1, int(self.config.get("max_achievement_notifications", 5)))
        self.SGDB_API_KEY = self.config.get("sgdb_api_key", "")

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        if getattr(self, "_ssm_running", False):
            logger.error("当前插件已在运行中。请重启 AstrBot 而非重载插件")
            return

        self._ssm_running = True
        self.context = context
        self.config = config or {}

        if "steam_ids" in self.config and "group_steam_ids" not in self.config:
            steam_ids = self.config.get("steam_ids", [])
            if isinstance(steam_ids, str):
                steam_ids = [item.strip() for item in steam_ids.split(",") if item.strip()]
            self.config["group_steam_ids"] = {"default": steam_ids}
            self.config.pop("steam_ids", None)

        self.group_steam_ids = {}
        self.group_last_states = {}
        self.group_start_play_times = {}
        self.group_last_quit_times = {}
        self.group_pending_logs = {}
        self.group_recent_games = {}
        self.group_pending_quit = {}
        self.next_poll_time = {}
        self.notify_sessions = {}
        self.running_groups = set()
        self.group_monitor_enabled = {}
        self.group_achievement_enabled = {}
        self.achievement_poll_tasks = {}
        self.achievement_snapshots = {}
        self.achievement_fail_count = {}
        self.achievement_blacklist = set()
        self._recent_start_notify = {}
        self._superpower_cache = {}
        self._abilities = None
        self._abilities_path = Path(__file__).resolve().parent / "abilities.txt"
        self._game_name_cache = {}
        self._online_count_cache = {}
        self._dirty = False
        self.recent_events = deque(maxlen=200)
        self.log_throttle = LogThrottle(interval_seconds=300)
        self.request_semaphore = asyncio.Semaphore(5)
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=8.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._pending_quit_tasks = {}

        self.data_dir = StarTools.get_data_dir("steam_status_monitor")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_service = SteamConfigService(self)
        normalized_incoming_config = self.config_service.normalize_incoming_config(self.config)
        self.config = self.config_service.load_merged_config(normalized_incoming_config)
        self.config_service.save_runtime_config(self.config)
        self._ensure_fonts()

        self._sync_runtime_from_config()
        self._log_api_key_status("startup", normalized_incoming_config)
        self._load_group_steam_ids()
        self._load_persistent_data()
        self._load_notify_session()

        steam_group_mapping = self.config.get("steam_group_mapping", [])
        if steam_group_mapping:
            self._process_steam_group_mapping(steam_group_mapping)

        self.achievement_monitor = AchievementMonitor(self.data_dir)
        self.achievement_monitor.enable_failure_blacklist = self.enable_failure_blacklist
        self.steam_api_service = SteamApiService(self.http_client, self.request_semaphore)
        self.steam_id_service = SteamIdService(self)
        self.steam_monitor_service = SteamMonitorService(self)
        self.steam_notification_service = SteamNotificationService(self)

        if self.notify_sessions and self.API_KEY and self.group_steam_ids:
            for group_id in self.notify_sessions:
                if group_id in self.group_steam_ids:
                    self.running_groups.add(group_id)

        self.save_task = asyncio.create_task(self._periodic_save_loop())
        self.global_poll_task = asyncio.create_task(self.global_poll_and_log_loop())
        self.init_task = asyncio.create_task(self.init_poll_time_once())

    async def terminate(self):
        tasks = [self.save_task, self.global_poll_task, self.init_task]
        for task in tasks:
            if task and not task.done():
                task.cancel()
        for task in list(self.achievement_poll_tasks.values()):
            task.cancel()
        for task_map in self._pending_quit_tasks.values():
            for task in task_map.values():
                task.cancel()
        self._save_persistent_data()
        try:
            await self.http_client.aclose()
        except Exception:
            pass

    async def _send_notification(self, group_id: str, text: str | None = None, image_bytes: bytes | None = None) -> bool:
        return await self.steam_notification_service.send_notification(group_id, text=text, image_bytes=image_bytes)

    async def _send_llm_wrapped_notification(
        self,
        group_id: str,
        tool_name: str,
        raw_payload: str,
        image_bytes: bytes | None = None,
    ) -> bool:
        return await self.steam_notification_service.send_llm_wrapped_notification(
            group_id,
            tool_name,
            raw_payload,
            image_bytes=image_bytes,
        )

    async def _send_image_to_event(self, event: AstrMessageEvent, image_bytes: bytes | None) -> bool:
        return await self.steam_notification_service.send_image_to_event(event, image_bytes)

    async def _summarize_tool_result_with_llm(self, tool_name: str, raw_payload: str, *, image_sent: bool = False) -> str:
        return await self.steam_notification_service.summarize_tool_result_with_llm(
            tool_name,
            raw_payload,
            image_sent=image_sent,
        )

    async def _request_llm_from_tool_result(self, event: AstrMessageEvent, tool_name: str, raw_payload: str):
        curr_cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
        conversation = None
        contexts = []
        if curr_cid:
            conversation = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, curr_cid)
            if conversation and getattr(conversation, "history", None):
                contexts = json.loads(conversation.history)

        prompt = (
            f"以下是工具 {tool_name} 的原始结果，请严格基于这些结果继续回复用户。"
            "不要把原始 JSON 原样照搬给用户，要结合当前对话上下文、用户当前人格设定和真实结果自然作答。"
            "禁止编造结果中不存在的数据。\n\n"
            f"{raw_payload}"
        )
        yield event.request_llm(
            prompt=prompt,
            func_tool_manager=self.context.get_llm_tool_manager(),
            session_id=curr_cid,
            contexts=contexts,
            system_prompt="",
            image_urls=[],
            conversation=conversation,
        )

    async def refresh_all_monitored_status(self):
        refreshed_group_count = 0
        total_steam_id_count = 0
        for group_id, steam_ids in self.group_steam_ids.items():
            if not steam_ids:
                continue
            status_map = await self._fetch_player_status_batch(steam_ids, retry=1)
            if not status_map:
                continue
            refreshed_group_count += 1
            total_steam_id_count += len(status_map)
            self.group_last_states.setdefault(group_id, {}).update(status_map)
            for sid, status in status_map.items():
                self._update_next_poll_time(group_id, sid, status)
            self._mark_dirty()
        return {
            "refreshed_group_count": refreshed_group_count,
            "refreshed_steam_id_count": total_steam_id_count,
        }

    def _build_tip_text(self, duration_min: float) -> str:
        if duration_min < 5:
            return "风扇都没转热，主人就结束了？"
        if duration_min < 10:
            return "杂鱼杂鱼~主人你就这水平？"
        if duration_min < 30:
            return "热身一下就结束了？"
        if duration_min < 60:
            return "歇会儿再来，别太累了喵！"
        if duration_min < 120:
            return "沉浸在游戏世界，时间过得飞快喵！"
        if duration_min < 300:
            return "肝到手软了喵！主人不如陪陪咱~"
        if duration_min < 600:
            return "你吃饭了吗？还是说你已经忘了吃饭这件事？"
        if duration_min < 1200:
            return "家里电费都要被你玩光了喵！"
        if duration_min < 1800:
            return "咱都要给你颁发‘不眠猫’勋章了！"
        if duration_min < 2400:
            return "主人你还活着喵？你是不是忘了关电脑呀~"
        return "你已经和椅子合为一体，成为传说中的‘椅子精’了喵！"

    async def _fetch_player_status_batch(self, steam_ids, retry=None):
        unique_ids = [sid for sid in dict.fromkeys(str(item) for item in steam_ids if str(item).strip())]
        if not unique_ids or not self.API_KEY:
            return {}
        retry_times = retry if retry is not None else self.RETRY_TIMES
        delay_seconds = 1
        original_key_index = self._current_api_key_index
        url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        params = {"key": self.API_KEY, "steamids": ",".join(unique_ids)}

        for attempt in range(retry_times):
            try:
                status_code, data = await fetch_json(
                    self.http_client,
                    url,
                    params=params,
                    timeout=15,
                    semaphore=self.request_semaphore,
                )
                if status_code == 403:
                    current_masked = self.config_service.mask_secret(self.API_KEY)
                    next_key = self._get_next_api_key()
                    if next_key and self._current_api_key_index != original_key_index:
                        self.API_KEY = next_key
                        params["key"] = self.API_KEY
                        logger.warning(
                            "[steam_status_monitor] GetPlayerSummaries 返回 403，准备轮换 API Key。old_index=%s old_key=%s new_index=%s new_key=%s"
                            % (
                                original_key_index,
                                current_masked,
                                self._current_api_key_index,
                                self.config_service.mask_secret(self.API_KEY),
                            )
                        )
                        await asyncio.sleep(delay_seconds)
                        delay_seconds = min(delay_seconds * 2, 8)
                        continue
                    self.log_throttle.log(
                        "steam_status_403",
                        logger.warning,
                        f"Steam 状态接口返回 403，所有 API Key 已尝试。current_index={self._current_api_key_index} current_key={current_masked}",
                    )
                    return {}
                if status_code != 200 or not data:
                    raise RuntimeError(f"HTTP {status_code}")
                players = data.get("response", {}).get("players", [])
                result = {}
                for player in players:
                    steamid = str(player.get("steamid", "")).strip()
                    if not steamid:
                        continue
                    result[steamid] = {
                        "name": player.get("personaname"),
                        "gameid": player.get("gameid"),
                        "lastlogoff": player.get("lastlogoff"),
                        "gameextrainfo": player.get("gameextrainfo"),
                        "personastate": player.get("personastate", 0),
                        "avatarfull": player.get("avatarfull"),
                        "avatar": player.get("avatar"),
                    }
                return result
            except Exception as error:
                if attempt == retry_times - 1:
                    self.log_throttle.log(
                        f"steam_status_batch:{','.join(unique_ids[:3])}",
                        logger.warning,
                        f"批量获取 Steam 状态失败: {error}",
                    )
                else:
                    await asyncio.sleep(delay_seconds)
                    delay_seconds = min(delay_seconds * 2, 8)
        return {}

    async def fetch_player_status(self, steam_id, retry=None):
        result = await self._fetch_player_status_batch([steam_id], retry=retry)
        return result.get(str(steam_id))

    async def _fetch_game_names_from_store(self, gid: str, fallback_name=None):
        url_zh = f"https://store.steampowered.com/api/appdetails?appids={gid}&l=schinese"
        url_en = f"https://store.steampowered.com/api/appdetails?appids={gid}&l=en"
        name_zh = fallback_name or "未知游戏"
        name_en = fallback_name or "未知游戏"
        try:
            _, data_zh = await fetch_json(self.http_client, url_zh, timeout=10, semaphore=self.request_semaphore)
            if data_zh:
                info_zh = data_zh.get(gid, {}).get("data", {})
                name_zh = info_zh.get("name") or name_zh
            _, data_en = await fetch_json(self.http_client, url_en, timeout=10, semaphore=self.request_semaphore)
            if data_en:
                info_en = data_en.get(gid, {}).get("data", {})
                name_en = info_en.get("name") or name_en
        except Exception as error:
            self.log_throttle.log(f"game_name:{gid}", logger.warning, f"获取游戏名失败: {error}")
        self._game_name_cache[gid] = {"timestamp": time.time(), "zh": name_zh, "en": name_en}
        return name_zh, name_en

    async def get_game_names(self, gameid, fallback_name=None):
        if not gameid:
            default_name = fallback_name or "未知游戏"
            return default_name, default_name
        gid = str(gameid)
        cached = self._game_name_cache.get(gid)
        if cached and time.time() - cached.get("timestamp", 0) < 86400:
            return cached["zh"], cached["en"]
        return await self._fetch_game_names_from_store(gid, fallback_name)

    async def get_chinese_game_name(self, gameid, fallback_name=None):
        name_zh, _ = await self.get_game_names(gameid, fallback_name)
        return name_zh

    async def get_game_online_count(self, gameid):
        if not gameid:
            return None
        gid = str(gameid)
        cached = self._online_count_cache.get(gid)
        if cached and time.time() - cached.get("timestamp", 0) < 300:
            return cached["count"]
        url = f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={gid}"
        try:
            status_code, data = await fetch_json(
                self.http_client,
                url,
                timeout=10,
                semaphore=self.request_semaphore,
            )
            if status_code == 200 and data:
                count = data.get("response", {}).get("player_count")
                self._online_count_cache[gid] = {"timestamp": time.time(), "count": count}
                return count
        except Exception as error:
            self.log_throttle.log(f"online_count:{gid}", logger.warning, f"获取在线人数失败: {error}")
        return None

    def crop_image_auto(self, img_path_or_bytes, bg_color=(20, 26, 33), threshold=25):
        if isinstance(img_path_or_bytes, PILImage.Image):
            image = img_path_or_bytes.convert("RGB")
        elif isinstance(img_path_or_bytes, bytes):
            image = PILImage.open(io.BytesIO(img_path_or_bytes)).convert("RGB")
        else:
            image = PILImage.open(img_path_or_bytes).convert("RGB")

        background = PILImage.new("RGB", image.size, bg_color)
        diff = ImageChops.difference(image, background)
        channels = diff.split()
        binary_mask = PILImage.merge(
            "RGB",
            tuple(channel.point(lambda value: 255 if value > threshold else 0) for channel in channels),
        )
        bbox = binary_mask.getbbox()
        if not bbox:
            return image
        return image.crop(bbox)

    async def init_poll_time_once(self):
        await asyncio.sleep(5)
        if not self.running_groups:
            return
        for group_id in list(self.running_groups):
            steam_ids = self.group_steam_ids.get(group_id, [])
            if not steam_ids:
                continue
            status_map = await self._fetch_player_status_batch(steam_ids, retry=1)
            if not status_map:
                continue
            self.group_last_states.setdefault(group_id, {}).update(status_map)
            now = int(time.time())
            for sid, status in status_map.items():
                if status.get("gameid"):
                    self.group_start_play_times.setdefault(group_id, {}).setdefault(sid, {})
                    self.group_start_play_times[group_id][sid][str(status["gameid"])] = now
                self._update_next_poll_time(group_id, sid, status)
            self._mark_dirty()

    def _update_next_poll_time(self, group_id: str, sid: str, status: dict):
        now = int(time.time())
        gameid = status.get("gameid")
        personastate = status.get("personastate", 0)
        lastlogoff = status.get("lastlogoff")

        if self.fixed_poll_interval > 0:
            poll_interval = self.fixed_poll_interval
        elif gameid:
            poll_interval = 60
        elif personastate and int(personastate) > 0:
            poll_interval = 60
        elif lastlogoff:
            hours_ago = (now - int(lastlogoff)) / 3600
            if hours_ago <= 0.2:
                poll_interval = 60
            elif hours_ago <= 3:
                poll_interval = 300
            elif hours_ago <= 24:
                poll_interval = 600
            elif hours_ago <= 48:
                poll_interval = 1200
            else:
                poll_interval = 1800
        else:
            poll_interval = 1800

        self.next_poll_time.setdefault(group_id, {})[sid] = now + poll_interval

    async def global_poll_and_log_loop(self):
        while True:
            now = time.time()
            next_minute = (int(now) // 60 + 1) * 60
            await asyncio.sleep(max(0, next_minute - now))
            if not self.running_groups:
                continue
            all_logs = []
            for group_id in list(self.running_groups):
                if not self.group_monitor_enabled.get(group_id, True):
                    continue
                steam_ids = self.group_steam_ids.get(group_id, [])
                if not steam_ids:
                    continue
                due_ids = []
                next_poll = self.next_poll_time.setdefault(group_id, {})
                current_time = int(time.time())
                for sid in steam_ids:
                    if current_time >= next_poll.get(sid, 0):
                        due_ids.append(sid)
                if not due_ids:
                    continue
                status_map = await self._fetch_player_status_batch(due_ids)
                if not status_map:
                    continue
                log_text = await self.check_status_change(group_id, status_override=status_map)
                if log_text and self.detailed_poll_log:
                    all_logs.append(f"群{group_id}：\n{log_text}")
            if all_logs and self.detailed_poll_log:
                logger.info("====== Steam 状态监控轮询日志 ======\n" + "\n".join(all_logs))

    async def achievement_periodic_check(self, group_id, sid, gameid, player_name, game_name):
        key = (group_id, sid, gameid)
        try:
            while True:
                await asyncio.sleep(1200)
                if not self.group_achievement_enabled.get(group_id, True):
                    return
                achievements_a = self.achievement_snapshots.get(key)
                achievements_b = await self.achievement_monitor.get_player_achievements(
                    self.API_KEY,
                    group_id,
                    sid,
                    gameid,
                    http_client=self.http_client,
                    request_semaphore=self.request_semaphore,
                )
                today = time.strftime("%Y-%m-%d")
                fail_key = (gameid, today)
                if achievements_b is None:
                    failure_count = self.achievement_fail_count.get(fail_key, 0) + 1
                    self.achievement_fail_count[fail_key] = failure_count
                    if failure_count >= 10 and self.enable_failure_blacklist:
                        self.achievement_blacklist.add(gameid)
                        return
                    continue
                if achievements_a is not None:
                    new_achievements = set(achievements_b) - set(achievements_a)
                    if new_achievements:
                        await self.notify_new_achievements(group_id, sid, player_name, gameid, game_name, new_achievements)
                        self.achievement_snapshots[key] = list(achievements_b)
        except asyncio.CancelledError:
            return
        except Exception as error:
            self.log_throttle.log(f"achievement_period:{group_id}:{sid}:{gameid}", logger.warning, f"成就轮询异常: {error}")

    async def achievement_delayed_final_check(self, group_id, sid, gameid, player_name, game_name):
        key = (group_id, sid, gameid)
        await asyncio.sleep(300)
        if not self.group_achievement_enabled.get(group_id, True):
            self.achievement_snapshots.pop(key, None)
            self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)
            return

        achievements_a = self.achievement_snapshots.get(key)
        achievements_b = await self.achievement_monitor.get_player_achievements(
            self.API_KEY,
            group_id,
            sid,
            gameid,
            http_client=self.http_client,
            request_semaphore=self.request_semaphore,
        )
        today = time.strftime("%Y-%m-%d")
        fail_key = (gameid, today)
        if achievements_b is None:
            failure_count = self.achievement_fail_count.get(fail_key, 0) + 1
            self.achievement_fail_count[fail_key] = failure_count
            if failure_count >= 10 and self.enable_failure_blacklist:
                self.achievement_blacklist.add(gameid)
        elif achievements_a is not None:
            new_achievements = set(achievements_b) - set(achievements_a)
            if new_achievements:
                await self.notify_new_achievements(group_id, sid, player_name, gameid, game_name, new_achievements)

        self.achievement_snapshots.pop(key, None)
        self.achievement_poll_tasks.pop(key, None)
        self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)

    async def notify_new_achievements(self, group_id, steamid, player_name, gameid, game_name, new_achievements):
        if not self.group_achievement_enabled.get(group_id, True):
            return
        if not new_achievements or group_id not in self.notify_sessions:
            return

        achievements_to_notify = list(new_achievements)[: self.max_achievement_notifications]
        details = self.achievement_monitor.details_cache.get((group_id, gameid))
        if not details:
            details = await self.achievement_monitor.get_achievement_details(
                group_id,
                gameid,
                lang="schinese",
                api_key=self.API_KEY,
                steamid=steamid,
                http_client=self.http_client,
                request_semaphore=self.request_semaphore,
            )
        if details and game_name:
            for detail in details.values():
                detail["game_name"] = game_name

        image_bytes = None
        if details:
            unlocked_set = await self.achievement_monitor.get_player_achievements(
                self.API_KEY,
                group_id,
                steamid,
                gameid,
                http_client=self.http_client,
                request_semaphore=self.request_semaphore,
            )
            if not unlocked_set:
                cache_key = (group_id, steamid, gameid)
                unlocked_set = set(self.achievement_snapshots.get(cache_key, []))
            try:
                image_bytes = await self.achievement_monitor.render_achievement_image(
                    details,
                    set(achievements_to_notify),
                    player_name=player_name,
                    steamid=steamid,
                    appid=gameid,
                    unlocked_set=unlocked_set or set(),
                    font_path=self.get_font_path("NotoSansHans-Regular.otf"),
                    http_client=self.http_client,
                    request_semaphore=self.request_semaphore,
                )
            except Exception as error:
                self.log_throttle.log(f"achievement_render:{group_id}:{steamid}:{gameid}", logger.warning, f"成就图片渲染失败: {error}")

        raw_lines = [f"{player_name} 在 {game_name} 解锁了新成就。"]
        for achievement in achievements_to_notify:
            raw_lines.append(f"- {achievement}")
        if image_bytes:
            await self._send_notification(group_id, image_bytes=image_bytes)
        else:
            await self._send_notification(group_id, text="\n".join(raw_lines))

        self._record_event(
            group_id,
            steamid,
            player_name,
            "achievement",
            f"{player_name} 在 {game_name} 解锁了成就：{', '.join(achievements_to_notify)}",
            gameid=str(gameid),
            game_name=game_name,
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam on")
    async def steam_on(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        result = await self.steam_monitor_service.enable_monitor(group_id, event.unified_msg_origin)
        if not result["ok"]:
            if result["reason"] == "missing_api_key":
                yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
                return
            yield event.plain_result("本群未设置监控的 SteamID 列表，请先使用 /steam addid 添加。")
            return
        yield event.plain_result("本群 Steam 状态监控已启动。")

    @filter.command("steam addid")
    async def steam_addid(self, event: AstrMessageEvent, steamid: str):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        result = self.steam_id_service.add_ids(group_id, steamid)
        if not result["ok"]:
            yield event.plain_result(f"以下 SteamID 无效：{', '.join(result['invalid_ids'])}")
            return

        messages = []
        if result["added"]:
            messages.append(f"已添加: {', '.join(result['added'])}")
        if result["existed"]:
            messages.append(f"已存在: {', '.join(result['existed'])}")
        if not messages:
            messages.append("没有新增任何 SteamID。")
        yield event.plain_result("\n".join(messages))

    @filter.command("steam delid")
    async def steam_delid(self, event: AstrMessageEvent, steamid: str):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        result = self.steam_id_service.remove_id(group_id, steamid)
        if not result["ok"]:
            yield event.plain_result("该 SteamID 不在本群监控列表中。")
            return
        yield event.plain_result(f"已删除 SteamID: {steamid}")

    @filter.command("steam list")
    async def steam_list(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        steam_ids = self.group_steam_ids.get(group_id, [])
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        if not steam_ids:
            yield event.plain_result("本群未设置监控的 SteamID 列表，请先添加。")
            return
        async for result in handle_steam_list(self, event, group_id=group_id, font_path=self.get_font_path("NotoSansHans-Regular.otf")):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam config")
    async def steam_config(self, event: AstrMessageEvent):
        lines = []
        hidden_keys = {"steam_api_key", "sgdb_api_key"}
        for key, value in self.config.items():
            if key in hidden_keys:
                lines.append(f"{key}: ******")
            else:
                lines.append(f"{key}: {value}")
        yield event.plain_result("当前配置：\n" + "\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam api_status")
    async def steam_api_status(self, event: AstrMessageEvent):
        runtime_config = self.config_service.load_runtime_config()
        runtime_keys = normalize_api_keys(runtime_config.get("steam_api_key", []))
        active_keys = list(self.steam_api_keys)
        current_index = self._current_api_key_index if active_keys else -1
        current_key = active_keys[current_index] if active_keys and 0 <= current_index < len(active_keys) else ""

        lines = [
            "Steam API 状态诊断：",
            f"- 当前生效 key 数量: {len(active_keys)}",
            f"- 当前轮换索引: {current_index}",
            f"- 当前 key: {self.config_service.mask_secret(current_key)}",
            f"- runtime_config key 数量: {len(runtime_keys)}",
            f"- runtime_config keys: {self.config_service.mask_secret(runtime_keys)}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam set")
    async def steam_set(self, event: AstrMessageEvent, key: str, value: str):
        if key not in self.config:
            yield event.plain_result(f"无效参数: {key}")
            return
        old_value = self.config[key]
        new_value = value
        try:
            if isinstance(old_value, bool):
                new_value = value.strip().lower() in {"1", "true", "yes", "on", "y"}
            elif isinstance(old_value, int):
                new_value = int(value)
            elif isinstance(old_value, float):
                new_value = float(value)
            elif isinstance(old_value, list):
                new_value = [item.strip() for item in value.split(",") if item.strip()]
        except Exception:
            yield event.plain_result("参数类型错误。")
            return

        self.config_service.set_value(self.config, key, new_value)
        self._sync_runtime_from_config()
        self.achievement_monitor.enable_failure_blacklist = self.enable_failure_blacklist
        yield event.plain_result(f"已设置 {key} = {new_value}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam rs")
    async def steam_rs(self, event: AstrMessageEvent):
        self.steam_monitor_service.reset_runtime_state()
        yield event.plain_result("Steam 状态监控缓存与状态已重置。")

    @filter.command("steam help")
    async def steam_help(self, event: AstrMessageEvent):
        help_text = (
            "Steam 状态监控插件指令：\n"
            "/steam on\n"
            "/steam off\n"
            "/steam list\n"
            "/steam alllist\n"
            "/steam config\n"
            "/steam set [参数] [值]\n"
            "/steam addid [SteamID]\n"
            "/steam delid [SteamID]\n"
            "/steam openbox [SteamID]\n"
            "/steam rs\n"
            "/steam clear_allids\n"
            "/steam achievement_on\n"
            "/steam achievement_off\n"
            "/steam test_achievement_render [steamid] [gameid] [数量]\n"
            "/steam test_game_start_render [steamid] [gameid]\n"
            "/steam test_game_end_render [steamid] [gameid]\n"
            "/steam清除缓存"
        )
        yield event.plain_result(help_text)

    @filter.command("steam openbox")
    async def steam_openbox(self, event: AstrMessageEvent, steamid: str):
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        async for result in handle_openbox(self, event, steamid):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam off")
    async def steam_off(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        self.steam_monitor_service.disable_monitor(group_id)
        yield event.plain_result("本群 Steam 监控已关闭。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam achievement_on")
    async def steam_achievement_on(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        self.steam_monitor_service.enable_achievement(group_id)
        yield event.plain_result("本群 Steam 成就推送已开启。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam achievement_off")
    async def steam_achievement_off(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        self.steam_monitor_service.disable_achievement(group_id)
        yield event.plain_result("本群 Steam 成就推送已关闭。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam test_achievement_render")
    async def steam_test_achievement_render(self, event: AstrMessageEvent, steamid: str, gameid: int, count: int = 3):
        group_id = str(event.get_group_id()) if hasattr(event, "get_group_id") else "default"
        achievements = await self.achievement_monitor.get_player_achievements(
            self.API_KEY,
            group_id,
            steamid,
            gameid,
            http_client=self.http_client,
            request_semaphore=self.request_semaphore,
        )
        if not achievements:
            yield event.plain_result("未获取到任何成就。")
            return
        details = await self.achievement_monitor.get_achievement_details(
            group_id,
            gameid,
            lang="schinese",
            api_key=self.API_KEY,
            steamid=steamid,
            http_client=self.http_client,
            request_semaphore=self.request_semaphore,
        )
        sample_count = max(1, min(count, len(achievements)))
        import random
        unlocked = set(random.sample(list(achievements), sample_count))
        try:
            image_bytes = await self.achievement_monitor.render_achievement_image(
                details,
                unlocked,
                player_name=steamid,
                font_path=self.get_font_path("NotoSansHans-Regular.otf"),
                http_client=self.http_client,
                request_semaphore=self.request_semaphore,
            )
            yield event.image_result(create_temp_png(image_bytes))
        except Exception as error:
            yield event.plain_result(f"渲染异常: {error}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam test_game_start_render")
    async def test_game_start_render(self, event: AstrMessageEvent, steamid: str, gameid: int):
        try:
            status = await self.fetch_player_status(steamid, retry=1)
            player_name = status.get("name") if status else steamid
            avatar_url = (status.get("avatarfull") or status.get("avatar")) if status else ""
            zh_game_name, en_game_name = await self.get_game_names(gameid)
            image_bytes = await render_game_start(
                self.data_dir,
                steamid,
                player_name,
                avatar_url,
                gameid,
                zh_game_name,
                api_key=self.API_KEY,
                superpower=self.get_today_superpower(steamid),
                online_count=await self.get_game_online_count(gameid),
                sgdb_api_key=self.SGDB_API_KEY,
                font_path=self.get_font_path("NotoSansHans-Regular.otf"),
                sgdb_game_name=en_game_name,
                appid=gameid,
                http_client=self.http_client,
                request_semaphore=self.request_semaphore,
            )
            image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
            cropped = self.crop_image_auto(image, bg_color=(51, 81, 66), threshold=15)
            temp_path = create_temp_png(image_bytes)
            cropped.save(temp_path, format="PNG")
            yield event.image_result(temp_path)
        except Exception as error:
            yield event.plain_result(f"渲染异常: {error}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam test_game_end_render")
    async def steam_test_game_end_render(self, event: AstrMessageEvent, steamid: str, gameid: int, duration_min: float = 120, end_time: str = None, tip_text: str = None):
        try:
            status = await self.fetch_player_status(steamid, retry=1)
            player_name = status.get("name") if status else steamid
            avatar_url = (status.get("avatarfull") or status.get("avatar")) if status else ""
            zh_game_name, en_game_name = await self.get_game_names(gameid)
            end_time_str = end_time or datetime.now().strftime("%Y-%m-%d %H:%M")
            duration_h = float(duration_min) / 60 if duration_min else 0
            image_bytes = await render_game_end(
                self.data_dir,
                steamid,
                player_name,
                avatar_url,
                gameid,
                zh_game_name,
                end_time_str,
                tip_text or self._build_tip_text(duration_min),
                duration_h,
                sgdb_api_key=self.SGDB_API_KEY,
                font_path=self.get_font_path("NotoSansHans-Regular.otf"),
                sgdb_game_name=en_game_name,
                appid=gameid,
                http_client=self.http_client,
                request_semaphore=self.request_semaphore,
            )
            yield event.plain_result(f"{player_name} 不玩 {zh_game_name} 了\n游玩时间 {duration_h:.1f}小时")
            yield event.image_result(create_temp_png(image_bytes))
        except Exception as error:
            yield event.plain_result(f"渲染异常: {error}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam清除缓存")
    async def steam_clear_cache(self, event: AstrMessageEvent):
        cache_dirs = [
            self.data_dir / "avatars",
            self.data_dir / "covers",
            self.data_dir / "covers_v",
        ]
        cleared = []
        for directory in cache_dirs:
            if directory.exists():
                shutil.rmtree(directory)
                cleared.append(str(directory))
        if cleared:
            yield event.plain_result("已清除缓存目录：\n" + "\n".join(cleared))
        else:
            yield event.plain_result("未找到可清理的缓存目录。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam clear_allids")
    async def steam_clear_allids(self, event: AstrMessageEvent):
        self.steam_id_service.clear_ids()
        self.group_last_states.clear()
        self.group_start_play_times.clear()
        self.group_last_quit_times.clear()
        self.group_pending_logs.clear()
        self.group_pending_quit.clear()
        self.group_recent_games.clear()
        self.next_poll_time.clear()
        self.notify_sessions.clear()
        self.running_groups.clear()
        self._save_notify_session()
        self._mark_dirty()
        yield event.plain_result("已清空所有群聊的 SteamID 与状态数据。")

    async def _delayed_quit_check(self, group_id, sid, gameid):
        await asyncio.sleep(180)
        info = self.group_pending_quit.get(group_id, {}).get(sid, {}).get(gameid)
        if not info or info.get("notified"):
            return

        info["notified"] = True
        duration_min = info.get("duration_min", 0)
        if duration_min == 0 and info.get("start_time") and info.get("quit_time"):
            duration_min = max(0, (info["quit_time"] - info["start_time"]) / 60)
            info["duration_min"] = duration_min

        duration_text = f"{duration_min:.1f}分钟" if duration_min < 60 else f"{duration_min / 60:.1f}小时"
        message = f"{info['name']} 结束了 {info['game_name']} 的游玩，本次游玩时长约为 {duration_text}。"

        avatar_url = None
        last_state = self.group_last_states.get(group_id, {}).get(sid)
        if last_state:
            avatar_url = last_state.get("avatarfull") or last_state.get("avatar")
        if not avatar_url:
            status_full = await self.fetch_player_status(sid, retry=1)
            if status_full:
                avatar_url = status_full.get("avatarfull") or status_full.get("avatar")

        zh_game_name, en_game_name = await self.get_game_names(gameid, info["game_name"])
        image_bytes = None
        try:
            image_bytes = await render_game_end(
                self.data_dir,
                sid,
                info["name"],
                avatar_url,
                gameid,
                zh_game_name,
                datetime.fromtimestamp(info["quit_time"]).strftime("%Y-%m-%d %H:%M"),
                info.get("tip_text") or self._build_tip_text(duration_min),
                duration_min / 60 if duration_min > 0 else 0,
                sgdb_api_key=self.SGDB_API_KEY,
                font_path=self.get_font_path("NotoSansHans-Regular.otf"),
                sgdb_game_name=en_game_name,
                appid=gameid,
                http_client=self.http_client,
                request_semaphore=self.request_semaphore,
            )
        except Exception as error:
            self.log_throttle.log(f"quit_render:{group_id}:{sid}:{gameid}", logger.warning, f"推送结束游戏图片失败: {error}")

        if image_bytes:
            await self._send_notification(group_id, image_bytes=image_bytes)
        else:
            await self._send_notification(group_id, text=message)
        self._record_event(
            group_id,
            sid,
            info["name"],
            "game_stop",
            f"{info['name']} 结束游玩 {zh_game_name}",
            game_name_cn=zh_game_name,
            game_name_en=en_game_name,
            duration_min=duration_min,
        )

        key = (group_id, sid, gameid)
        poll_task = self.achievement_poll_tasks.pop(key, None)
        if poll_task:
            poll_task.cancel()
        self.achievement_snapshots.pop(key, None)
        self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)
        if sid in self.group_pending_quit.get(group_id, {}):
            self.group_pending_quit[group_id][sid].pop(gameid, None)
        self._mark_dirty()

    async def check_status_change(self, group_id, single_sid=None, status_override=None, poll_level=None):
        now = int(time.time())
        if isinstance(status_override, dict) and single_sid is None:
            steam_ids = list(status_override.keys())
        else:
            steam_ids = [single_sid] if single_sid else self.group_steam_ids.get(group_id, [])

        last_states = self.group_last_states.setdefault(group_id, {})
        start_play_times = self.group_start_play_times.setdefault(group_id, {})
        last_quit_times = self.group_last_quit_times.setdefault(group_id, {})
        pending_quit = self.group_pending_quit.setdefault(group_id, {})
        msg_lines = []

        for sid in steam_ids:
            if isinstance(status_override, dict):
                status = status_override.get(sid)
            else:
                status = await self.fetch_player_status(sid)
            if not status:
                continue

            prev = last_states.get(sid)
            name = status.get("name") or sid
            gameid = str(status.get("gameid")) if status.get("gameid") else None
            game = status.get("gameextrainfo")
            lastlogoff = status.get("lastlogoff")
            personastate = status.get("personastate", 0)
            zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
            prev_gameid = str(prev.get("gameid")) if prev and prev.get("gameid") else None

            if prev_gameid and not gameid:
                if not isinstance(start_play_times.get(sid), dict):
                    start_play_times[sid] = {}
                start_time = start_play_times[sid].get(prev_gameid, now)
                duration_min = (now - start_time) / 60 if start_time else 0
                pending_quit.setdefault(sid, {})
                pending_quit[sid][prev_gameid] = {
                    "quit_time": now,
                    "name": name,
                    "game_name": await self.get_chinese_game_name(prev_gameid, prev.get("gameextrainfo") if prev else None),
                    "duration_min": duration_min,
                    "start_time": start_time,
                    "notified": False,
                }
                quit_task = self._pending_quit_tasks.setdefault(sid, {}).get(prev_gameid)
                if quit_task:
                    quit_task.cancel()
                self._pending_quit_tasks.setdefault(sid, {})[prev_gameid] = asyncio.create_task(
                    self._delayed_quit_check(group_id, sid, prev_gameid)
                )
                last_quit_times.setdefault(sid, {})
                last_quit_times[sid][prev_gameid] = now
                last_states[sid] = status
                self._mark_dirty()
                continue

            if gameid and gameid != prev_gameid:
                recent_key = (group_id, sid, gameid)
                last_start = self._recent_start_notify.get(recent_key)
                if last_start and now - last_start < 10:
                    last_states[sid] = status
                    self._update_next_poll_time(group_id, sid, status)
                    continue
                self._recent_start_notify[recent_key] = now
                pending_quit.setdefault(sid, {})
                quit_info = pending_quit[sid].get(gameid)
                if quit_info and now - quit_info["quit_time"] <= 180 and not quit_info.get("notified"):
                    task = self._pending_quit_tasks.get(sid, {}).get(gameid)
                    if task:
                        task.cancel()
                        self._pending_quit_tasks[sid].pop(gameid, None)
                    quit_info["notified"] = True
                    network_message = f"{name} 在游玩 {zh_game_name} 时发生了短暂网络波动。"
                    await self._send_llm_wrapped_notification(group_id, "network_flap", network_message)
                    self._record_event(group_id, sid, name, "network_flap", network_message, game_name_cn=zh_game_name)
                    last_states[sid] = status
                    self._update_next_poll_time(group_id, sid, status)
                    self._mark_dirty()
                    continue

                if not isinstance(start_play_times.get(sid), dict):
                    start_play_times[sid] = {}
                start_play_times[sid][gameid] = now
                zh_game_name, en_game_name = await self.get_game_names(gameid, zh_game_name)
                start_message = (
                    f"{name} 开始游玩 {zh_game_name}。"
                    f"\n游戏ID: {gameid}"
                    f"\n当前游戏在线人数: {await self.get_game_online_count(gameid)}"
                    f"\n今日超能力: {self.get_today_superpower(sid)}"
                )
                image_bytes = None
                try:
                    image_bytes = await render_game_start(
                        self.data_dir,
                        sid,
                        name,
                        status.get("avatarfull") or status.get("avatar"),
                        gameid,
                        zh_game_name,
                        api_key=self.API_KEY,
                        superpower=self.get_today_superpower(sid),
                        online_count=await self.get_game_online_count(gameid),
                        sgdb_api_key=self.SGDB_API_KEY,
                        font_path=self.get_font_path("NotoSansHans-Regular.otf"),
                        sgdb_game_name=en_game_name,
                        appid=gameid,
                        http_client=self.http_client,
                        request_semaphore=self.request_semaphore,
                    )
                except Exception as error:
                    self.log_throttle.log(f"start_render:{group_id}:{sid}:{gameid}", logger.warning, f"推送开始游戏图片失败: {error}")

                if image_bytes:
                    await self._send_notification(group_id, image_bytes=image_bytes)
                else:
                    await self._send_notification(group_id, text=start_message)
                self._record_event(
                    group_id,
                    sid,
                    name,
                    "game_start",
                    f"{name} 开始游玩 {zh_game_name}",
                    game_name_cn=zh_game_name,
                    game_name_en=en_game_name,
                )

                if self.group_achievement_enabled.get(group_id, True):
                    try:
                        cache_key = (group_id, sid, gameid)
                        achievements = await self.achievement_monitor.get_player_achievements(
                            self.API_KEY,
                            group_id,
                            sid,
                            gameid,
                            http_client=self.http_client,
                            request_semaphore=self.request_semaphore,
                        )
                        self.achievement_snapshots[cache_key] = list(achievements) if achievements else []
                        poll_task = asyncio.create_task(
                            self.achievement_periodic_check(group_id, sid, gameid, name, zh_game_name)
                        )
                        old_task = self.achievement_poll_tasks.get(cache_key)
                        if old_task:
                            old_task.cancel()
                        self.achievement_poll_tasks[cache_key] = poll_task
                    except Exception as error:
                        self.log_throttle.log(f"achievement_start:{group_id}:{sid}:{gameid}", logger.warning, f"启动成就监控失败: {error}")

                last_states[sid] = status
                self._update_next_poll_time(group_id, sid, status)
                self._mark_dirty()
                continue

            self._update_next_poll_time(group_id, sid, status)
            if self.detailed_poll_log:
                if gameid:
                    msg_lines.append(f"🟢【{name}】正在玩 {zh_game_name}")
                elif personastate and int(personastate) > 0:
                    msg_lines.append(f"🟡【{name}】在线")
                elif lastlogoff:
                    hours_ago = (now - int(lastlogoff)) / 3600
                    msg_lines.append(f"⚪️【{name}】离线，上次在线 {hours_ago:.1f} 小时前")
                else:
                    msg_lines.append(f"⚪️【{name}】离线")
            last_states[sid] = status

        self._mark_dirty()
        return "\n".join(msg_lines) if msg_lines else None

    @filter.command("steam alllist")
    async def steam_alllist(self, event: AstrMessageEvent):
        lines = []
        now = int(time.time())
        for group_id, steam_ids in self.group_steam_ids.items():
            lines.append(f"群组: {group_id}")
            last_states = self.group_last_states.get(group_id, {})
            next_poll = self.next_poll_time.get(group_id, {})
            for sid in steam_ids:
                status = last_states.get(sid)
                name = status.get("name") if status else sid
                gameid = status.get("gameid") if status else None
                game = status.get("gameextrainfo") if status else None
                lastlogoff = status.get("lastlogoff") if status else None
                personastate = status.get("personastate", 0) if status else 0
                next_time = next_poll.get(sid, now)
                seconds_left = max(0, int(next_time - now))
                poll_text = f"{seconds_left}秒后" if seconds_left < 60 else f"{seconds_left // 60}分钟后"
                if gameid:
                    state_text = f"正在玩 {await self.get_chinese_game_name(gameid, game)}"
                elif personastate and int(personastate) > 0:
                    state_text = "在线"
                elif lastlogoff:
                    hours_ago = (now - int(lastlogoff)) / 3600
                    state_text = f"离线，上次在线 {hours_ago:.1f} 小时前"
                else:
                    state_text = "离线"
                lines.append(f"  {name}({sid}) - {state_text}（下次轮询 {poll_text}）")
            lines.append("")
        yield event.plain_result("\n".join(lines))

    def get_today_superpower(self, steamid):
        today = datetime.now().date().isoformat()
        cache_key = (steamid, today)
        if cache_key in self._superpower_cache:
            return self._superpower_cache[cache_key]
        if self._abilities is None:
            self._abilities = load_abilities(self._abilities_path)
        superpower = get_daily_superpower(steamid, self._abilities)
        self._superpower_cache[cache_key] = superpower
        return superpower

    @filter.llm_tool(name="steam_query_monitor_overview")
    async def steam_query_monitor_overview_tool(self, event: AstrMessageEvent, group_id: str = "", limit: int = 5):
        """查询 Steam 监控总览信息，不用于玩家状态图片查询。

        Args:
            group_id(string): 群号，不填时返回全部群概览
            limit(number): 最近事件返回条数
        """
        refresh_result = await self.refresh_all_monitored_status()
        target_groups = [str(group_id)] if group_id else list(self.group_steam_ids.keys())

        groups = []
        for gid in target_groups:
            steam_ids = self.group_steam_ids.get(gid, [])
            groups.append(
                {
                    "group_id": gid,
                    "steam_ids": list(steam_ids),
                    "monitor_enabled": self.group_monitor_enabled.get(gid, True),
                    "achievement_enabled": self.group_achievement_enabled.get(gid, True),
                    "running": gid in self.running_groups,
                }
            )

        recent_events = []
        for item in reversed(list(self.recent_events)):
            if group_id and item["group_id"] != str(group_id):
                continue
            recent_events.append(item)
            if len(recent_events) >= max(1, int(limit)):
                break

        payload = {
            "refresh_result": refresh_result,
            "running_group_count": len(self.running_groups),
            "managed_group_count": len(self.group_steam_ids),
            "managed_steam_id_count": sum(len(ids) for ids in self.group_steam_ids.values()),
            "groups": groups,
            "recent_events": recent_events,
        }
        raw_payload = json.dumps(payload, ensure_ascii=False, indent=2)
        async for result in self._request_llm_from_tool_result(event, "steam_query_monitor_overview", raw_payload):
            yield result

    @filter.llm_tool(name="steam_query_recent_events")
    async def steam_query_recent_events_tool(self, event: AstrMessageEvent, group_id: str = "", limit: int = 10):
        """查询最近 Steam 状态事件。

        Args:
            group_id(string): 群号，不填时返回全部群事件
            limit(number): 返回条数
        """
        refresh_result = await self.refresh_all_monitored_status()
        events = []
        for item in reversed(list(self.recent_events)):
            if group_id and item["group_id"] != str(group_id):
                continue
            events.append(item)
            if len(events) >= max(1, int(limit)):
                break
        payload = {
            "refresh_result": refresh_result,
            "events": events,
        }
        raw_payload = json.dumps(payload, ensure_ascii=False, indent=2)
        async for result in self._request_llm_from_tool_result(event, "steam_query_recent_events", raw_payload):
            yield result

    @filter.llm_tool(name="steam_query_player_status")
    async def steam_query_player_status_tool(self, event: AstrMessageEvent, steamid: str):
        """查询指定 SteamID 的完整状态，并将完整聚合 payload 交给 LLM。

        Args:
            steamid(string): Steam64 位 ID
        """
        await self.refresh_all_monitored_status()
        payload = await self.steam_api_service.get_full_player_status(self.API_KEY, steamid)
        raw_payload = json.dumps(payload, ensure_ascii=False, indent=2)
        async for result in self._request_llm_from_tool_result(event, "steam_query_player_status", raw_payload):
            yield result

    @filter.llm_tool(name="steam_query_group_user_status")
    async def steam_query_group_user_status_tool(self, event: AstrMessageEvent, group_id: str = ""):
        """查询本群或指定群全部用户状态，并将完整状态 payload 交给 LLM。

        适用于：
        - 看看本群用户状态
        - 查询本群 steam 用户状态
        - 查看某个群所有玩家状态文本说明

        Args:
            group_id(string): 目标群号，不填时尽量使用当前群
        """
        target_group_id = str(group_id or (event.get_group_id() if hasattr(event, "get_group_id") else "default"))
        await self.refresh_all_monitored_status()
        steam_ids = self.group_steam_ids.get(target_group_id, [])
        players = []
        for sid in steam_ids:
            player_payload = await self.steam_api_service.get_full_player_status(self.API_KEY, sid)
            players.append(player_payload)

        payload = {
            "group_id": target_group_id,
            "steam_ids": list(steam_ids),
            "monitor_enabled": self.group_monitor_enabled.get(target_group_id, True),
            "achievement_enabled": self.group_achievement_enabled.get(target_group_id, True),
            "players": players,
        }
        raw_payload = json.dumps(payload, ensure_ascii=False, indent=2)
        async for result in self._request_llm_from_tool_result(event, "steam_query_group_user_status", raw_payload):
            yield result

    @filter.llm_tool(name="steam_query_group_binding_summary")
    async def steam_query_group_binding_summary_tool(self, event: AstrMessageEvent, group_id: str):
        """查询指定群的 SteamID 绑定摘要，不用于玩家状态图片查询。

        Args:
            group_id(string): 目标群号
        """
        steam_ids = self.group_steam_ids.get(str(group_id), [])
        if not steam_ids:
            raw_payload = f"群 {group_id} 当前没有绑定任何 SteamID。"
            async for result in self._request_llm_from_tool_result(event, "steam_query_group_binding_summary", raw_payload):
                yield result
            return

        raw_payload = f"群 {group_id} 当前绑定了 {len(steam_ids)} 个 SteamID：{', '.join(steam_ids)}"
        async for result in self._request_llm_from_tool_result(event, "steam_query_group_binding_summary", raw_payload):
            yield result

    @filter.llm_tool(name="steam_id_add")
    async def steam_id_add_tool(self, event: AstrMessageEvent, group_id: str, steamids: str):
        """向指定群添加 SteamID。

        Args:
            group_id(string): 目标群号
            steamids(string): 以逗号分隔的 SteamID 列表
        """
        result = self.steam_id_service.add_ids(group_id, steamids)
        if not result["ok"]:
            raw_payload = f"以下 SteamID 无效：{', '.join(result['invalid_ids'])}"
            async for item in self._request_llm_from_tool_result(event, "steam_id_add", raw_payload):
                yield item
            return

        lines = []
        if result["added"]:
            lines.append(f"已添加 SteamID：{', '.join(result['added'])}")
        if result["existed"]:
            lines.append(f"已存在的 SteamID：{', '.join(result['existed'])}")
        if not lines:
            lines.append("没有新增任何 SteamID。")
        async for item in self._request_llm_from_tool_result(event, "steam_id_add", "\n".join(lines)):
            yield item

    @filter.llm_tool(name="steam_id_remove")
    async def steam_id_remove_tool(self, event: AstrMessageEvent, group_id: str, steamid: str):
        """从指定群删除 SteamID。

        Args:
            group_id(string): 目标群号
            steamid(string): 需要删除的 SteamID
        """
        result = self.steam_id_service.remove_id(group_id, steamid)
        if not result["ok"]:
            async for item in self._request_llm_from_tool_result(event, "steam_id_remove", f"SteamID {steamid} 不在群 {group_id} 的监控列表中。"):
                yield item
            return
        async for item in self._request_llm_from_tool_result(event, "steam_id_remove", f"已从群 {group_id} 删除 SteamID {steamid}。"):
            yield item

    @filter.llm_tool(name="steam_id_list")
    async def steam_id_list_tool(self, event: AstrMessageEvent, group_id: str):
        """列出指定群的 SteamID 绑定列表。

        Args:
            group_id(string): 目标群号
        """
        result = self.steam_id_service.list_ids(group_id)
        if not result["steam_ids"]:
            async for item in self._request_llm_from_tool_result(event, "steam_id_list", f"群 {group_id} 当前没有绑定任何 SteamID。"):
                yield item
            return
        async for item in self._request_llm_from_tool_result(event, "steam_id_list", f"群 {group_id} 当前绑定的 SteamID 有：{', '.join(result['steam_ids'])}"):
            yield item

    @filter.llm_tool(name="steam_id_clear")
    async def steam_id_clear_tool(self, event: AstrMessageEvent, group_id: str = ""):
        """清空指定群或全部群的 SteamID。

        Args:
            group_id(string): 目标群号，不填时清空全部群
        """
        result = self.steam_id_service.clear_ids(group_id)
        if result["scope"] == "group":
            message = f"已清空群 {result['group_id']} 的所有 SteamID。"
        else:
            message = "已清空全部群的 SteamID。"
        async for item in self._request_llm_from_tool_result(event, "steam_id_clear", message):
            yield item

    @filter.llm_tool(name="steam_monitor_on")
    async def steam_monitor_on_tool(self, event: AstrMessageEvent, group_id: str):
        """开启指定群的 Steam 监控。

        Args:
            group_id(string): 目标群号
        """
        result = await self.steam_monitor_service.enable_monitor(group_id)
        if not result["ok"]:
            if result["reason"] == "missing_api_key":
                async for item in self._request_llm_from_tool_result(event, "steam_monitor_on", "未配置 Steam API Key，请先在插件配置中填写 steam_api_key。"):
                    yield item
                return
            async for item in self._request_llm_from_tool_result(event, "steam_monitor_on", f"群 {group_id} 尚未绑定任何 SteamID。"):
                yield item
            return
        async for item in self._request_llm_from_tool_result(event, "steam_monitor_on", f"已开启群 {group_id} 的 Steam 监控。"):
            yield item

    @filter.llm_tool(name="steam_monitor_off")
    async def steam_monitor_off_tool(self, event: AstrMessageEvent, group_id: str):
        """关闭指定群的 Steam 监控。

        Args:
            group_id(string): 目标群号
        """
        self.steam_monitor_service.disable_monitor(group_id)
        async for item in self._request_llm_from_tool_result(event, "steam_monitor_off", f"已关闭群 {group_id} 的 Steam 监控。"):
            yield item

    @filter.llm_tool(name="steam_achievement_on")
    async def steam_achievement_on_tool(self, event: AstrMessageEvent, group_id: str):
        """开启指定群的 Steam 成就推送。

        Args:
            group_id(string): 目标群号
        """
        self.steam_monitor_service.enable_achievement(group_id)
        async for item in self._request_llm_from_tool_result(event, "steam_achievement_on", f"已开启群 {group_id} 的 Steam 成就推送。"):
            yield item

    @filter.llm_tool(name="steam_achievement_off")
    async def steam_achievement_off_tool(self, event: AstrMessageEvent, group_id: str):
        """关闭指定群的 Steam 成就推送。

        Args:
            group_id(string): 目标群号
        """
        self.steam_monitor_service.disable_achievement(group_id)
        async for item in self._request_llm_from_tool_result(event, "steam_achievement_off", f"已关闭群 {group_id} 的 Steam 成就推送。"):
            yield item

    @filter.llm_tool(name="steam_monitor_reset")
    async def steam_monitor_reset_tool(self, event: AstrMessageEvent):
        """重置 Steam 监控缓存和状态。"""
        self.steam_monitor_service.reset_runtime_state()
        async for item in self._request_llm_from_tool_result(event, "steam_monitor_reset", "已重置 Steam 监控缓存和状态。"):
            yield item

    @filter.llm_tool(name="steam_image_player_status")
    async def steam_image_player_status_tool(self, event: AstrMessageEvent, steamid: str):
        """发送指定玩家的状态图片。

        Args:
            steamid(string): Steam64 位 ID
        """
        await self.refresh_all_monitored_status()
        payload = await self.steam_api_service.get_full_player_status(self.API_KEY, steamid)
        if not payload.get("available"):
            return
        profile = payload.get("profile", {})
        current_game = payload.get("current_game")
        if not current_game:
            return
        image_bytes = await render_game_start(
            self.data_dir,
            steamid,
            profile.get("personaname") or steamid,
            profile.get("avatarfull") or profile.get("avatar"),
            current_game.get("appid"),
            current_game.get("name") or "未知游戏",
            api_key=self.API_KEY,
            superpower=self.get_today_superpower(steamid),
            online_count=current_game.get("online_count"),
            sgdb_api_key=self.SGDB_API_KEY,
            font_path=self.get_font_path("NotoSansHans-Regular.otf"),
            sgdb_game_name=current_game.get("name"),
            appid=current_game.get("appid"),
            http_client=self.http_client,
            request_semaphore=self.request_semaphore,
        )
        await self._send_image_to_event(event, image_bytes)

    @filter.llm_tool(name="steam_image_group_status")
    async def steam_image_group_status_tool(self, event: AstrMessageEvent, group_id: str):
        """发送指定群的 Steam 玩家状态列表图片。

        适用于：
        - 看看这个群的steam玩家状态
        - 发这个群的steam状态图
        - 查看群玩家状态列表

        Args:
            group_id(string): 目标群号
        """
        await self.refresh_all_monitored_status()
        async for result in handle_steam_list(self, event, group_id=str(group_id), font_path=self.get_font_path("NotoSansHans-Regular.otf")):
            await event.send(result)