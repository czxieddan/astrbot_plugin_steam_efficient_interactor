import time
from typing import Optional

from .runtime_utils import create_temp_png
from .steam_list_render import render_steam_list_image


async def handle_steam_list(self, event, *, font_path: Optional[str] = None, **kwargs):
    group_id = kwargs.get("group_id")
    if not group_id:
        if hasattr(event, "get_group_id"):
            group_id = str(event.get_group_id())
        elif hasattr(event, "group_id"):
            group_id = str(event.group_id)
        else:
            group_id = "default"

    steam_ids = self.group_steam_ids.get(group_id, [])
    start_play_times = self.group_start_play_times.get(group_id, {})
    cached_states = self.group_last_states.get(group_id, {})
    user_list = []
    now = int(time.time())

    for sid in steam_ids:
        status = cached_states.get(sid)
        if not status:
            status = await self.fetch_player_status(sid, retry=1)
            if status:
                self.group_last_states.setdefault(group_id, {})[sid] = status

        if not status:
            user_list.append(
                {
                    "sid": sid,
                    "name": sid,
                    "status": "error",
                    "avatar_url": "",
                    "game": "",
                    "gameid": "",
                    "play_str": "获取失败",
                    "lastlogoff": None,
                }
            )
            continue

        name = status.get("name") or sid
        gameid = status.get("gameid")
        game = status.get("gameextrainfo")
        lastlogoff = status.get("lastlogoff")
        personastate = status.get("personastate", 0)
        avatar_url = status.get("avatarfull") or status.get("avatar") or ""
        zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")

        if gameid:
            start_time = None
            if isinstance(start_play_times.get(sid), dict):
                if gameid in start_play_times[sid]:
                    start_time = start_play_times[sid][gameid]
                elif start_play_times[sid]:
                    start_time = max(start_play_times[sid].values())
            else:
                start_time = start_play_times.get(sid)

            play_seconds = now - start_time if start_time else 0
            play_minutes = play_seconds / 60
            if play_minutes < 60:
                play_str = f"{play_minutes:.1f}分钟"
            else:
                play_str = f"{play_minutes / 60:.1f}小时"

            user_list.append(
                {
                    "sid": sid,
                    "name": name,
                    "status": "playing",
                    "avatar_url": avatar_url,
                    "game": zh_game_name,
                    "gameid": gameid,
                    "play_str": play_str,
                    "lastlogoff": lastlogoff,
                }
            )
        elif personastate and int(personastate) > 0:
            user_list.append(
                {
                    "sid": sid,
                    "name": name,
                    "status": "online",
                    "avatar_url": avatar_url,
                    "game": "",
                    "gameid": "",
                    "play_str": "",
                    "lastlogoff": lastlogoff,
                }
            )
        elif lastlogoff:
            hours_ago = (now - int(lastlogoff)) / 3600
            user_list.append(
                {
                    "sid": sid,
                    "name": name,
                    "status": "offline",
                    "avatar_url": avatar_url,
                    "game": "",
                    "gameid": "",
                    "play_str": f"上次在线 {hours_ago:.1f} 小时前",
                    "lastlogoff": lastlogoff,
                }
            )
        else:
            user_list.append(
                {
                    "sid": sid,
                    "name": name,
                    "status": "offline",
                    "avatar_url": avatar_url,
                    "game": "",
                    "gameid": "",
                    "play_str": "",
                    "lastlogoff": lastlogoff,
                }
            )

    image_bytes = await render_steam_list_image(
        self.data_dir,
        user_list,
        font_path=font_path,
        http_client=getattr(self, "http_client", None),
        request_semaphore=getattr(self, "request_semaphore", None),
    )
    if image_bytes:
        yield event.image_result(create_temp_png(image_bytes))
    else:
        yield event.plain_result("渲染图片失败")