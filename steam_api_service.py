from __future__ import annotations

import time
from typing import Any

import httpx

from .runtime_utils import fetch_json


class SteamApiService:
    def __init__(self, http_client: httpx.AsyncClient, request_semaphore=None):
        self.http_client = http_client
        self.request_semaphore = request_semaphore

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> tuple[int, Any | None]:
        return await fetch_json(
            self.http_client,
            url,
            params=params,
            timeout=15,
            semaphore=self.request_semaphore,
        )

    async def get_player_summary(self, api_key: str, steamid: str) -> tuple[dict[str, Any] | None, str | None]:
        url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        status_code, data = await self._get_json(url, params={"key": api_key, "steamids": steamid})
        if status_code != 200:
            return None, f"GetPlayerSummaries 返回 HTTP {status_code}"
        if not data:
            return None, "GetPlayerSummaries 返回空数据"
        players = data.get("response", {}).get("players", [])
        if not players:
            return None, "GetPlayerSummaries 未返回玩家信息"
        return players[0], None

    async def get_recent_games(self, api_key: str, steamid: str) -> tuple[list[dict[str, Any]], str | None]:
        url = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/"
        status_code, data = await self._get_json(url, params={"key": api_key, "steamid": steamid})
        if status_code != 200:
            return [], f"GetRecentlyPlayedGames 返回 HTTP {status_code}"
        if not data:
            return [], "GetRecentlyPlayedGames 返回空数据"
        return data.get("response", {}).get("games", []) or [], None

    async def get_owned_games(self, api_key: str, steamid: str, appid: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
        params = {"key": api_key, "steamid": steamid, "include_appinfo": 1}
        if appid:
            params["appids_filter[0]"] = appid
        status_code, data = await self._get_json(url, params=params)
        if status_code != 200:
            return [], f"GetOwnedGames 返回 HTTP {status_code}"
        if not data:
            return [], "GetOwnedGames 返回空数据"
        return data.get("response", {}).get("games", []) or [], None

    async def get_friend_list(self, api_key: str, steamid: str) -> tuple[list[dict[str, Any]], str | None]:
        url = "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"
        status_code, data = await self._get_json(url, params={"key": api_key, "steamid": steamid, "relationship": "friend"})
        if status_code != 200:
            return [], f"GetFriendList 返回 HTTP {status_code}"
        if not data:
            return [], "GetFriendList 返回空数据"
        return data.get("friendslist", {}).get("friends", []) or [], None

    async def get_badges(self, api_key: str, steamid: str) -> tuple[dict[str, Any], str | None]:
        url = "https://api.steampowered.com/IPlayerService/GetBadges/v1/"
        status_code, data = await self._get_json(url, params={"key": api_key, "steamid": steamid})
        if status_code != 200:
            return {}, f"GetBadges 返回 HTTP {status_code}"
        if not data:
            return {}, "GetBadges 返回空数据"
        return data.get("response", {}) or {}, None

    async def get_game_online_count(self, appid: str | int | None) -> tuple[int | None, str | None]:
        if not appid:
            return None, "未提供 appid"
        url = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
        status_code, data = await self._get_json(url, params={"appid": str(appid)})
        if status_code != 200:
            return None, f"GetNumberOfCurrentPlayers 返回 HTTP {status_code}"
        if not data:
            return None, "GetNumberOfCurrentPlayers 返回空数据"
        return data.get("response", {}).get("player_count"), None

    async def get_full_player_status(self, api_key: str, steamid: str) -> dict[str, Any]:
        summary, summary_error = await self.get_player_summary(api_key, steamid)
        current_game_id = str(summary.get("gameid")) if summary and summary.get("gameid") else None

        recent_games, recent_games_error = await self.get_recent_games(api_key, steamid)
        owned_games, owned_games_error = await self.get_owned_games(api_key, steamid, current_game_id)
        friend_list, friend_list_error = await self.get_friend_list(api_key, steamid)
        badges, badges_error = await self.get_badges(api_key, steamid)
        online_count, online_count_error = await self.get_game_online_count(current_game_id)

        owned_game = owned_games[0] if owned_games else {}
        playtime_minutes = owned_game.get("playtime_forever", 0) if owned_game else 0
        playtime_hours = round(playtime_minutes / 60, 1) if playtime_minutes else 0.0

        errors = []
        if summary_error:
            errors.append(summary_error)
        if recent_games_error:
            errors.append(recent_games_error)
        if owned_games_error:
            errors.append(owned_games_error)
        if friend_list_error:
            errors.append(friend_list_error)
        if badges_error:
            errors.append(badges_error)
        if online_count_error and current_game_id:
            errors.append(online_count_error)

        profile = {
            "personaname": summary.get("personaname") if summary else None,
            "profileurl": summary.get("profileurl") if summary else None,
            "avatar": summary.get("avatar") if summary else None,
            "avatarmedium": summary.get("avatarmedium") if summary else None,
            "avatarfull": summary.get("avatarfull") if summary else None,
            "realname": summary.get("realname") if summary else None,
            "timecreated": summary.get("timecreated") if summary else None,
            "loccountrycode": summary.get("loccountrycode") if summary else None,
            "locstatecode": summary.get("locstatecode") if summary else None,
            "loccityid": summary.get("loccityid") if summary else None,
            "communityvisibilitystate": summary.get("communityvisibilitystate") if summary else None,
            "profilestate": summary.get("profilestate") if summary else None,
            "commentpermission": summary.get("commentpermission") if summary else None,
            "primaryclanid": summary.get("primaryclanid") if summary else None,
            "personastateflags": summary.get("personastateflags") if summary else None,
        }

        status = {
            "personastate": summary.get("personastate", 0) if summary else None,
            "lastlogoff": summary.get("lastlogoff") if summary else None,
            "gameid": current_game_id,
            "gameextrainfo": summary.get("gameextrainfo") if summary else None,
        }

        current_game = None
        if current_game_id:
            current_game = {
                "appid": current_game_id,
                "name": summary.get("gameextrainfo") if summary else owned_game.get("name"),
                "online_count": online_count,
                "playtime_forever_minutes": playtime_minutes,
                "playtime_forever_hours": playtime_hours,
            }

        available_fields = {
            "summary": summary is not None,
            "recent_games": bool(recent_games) or recent_games_error is None,
            "owned_games": bool(owned_games) or owned_games_error is None,
            "friends": bool(friend_list) or friend_list_error is None,
            "badges": bool(badges) or badges_error is None,
            "game_online_count": current_game_id is None or online_count_error is None,
        }

        available = any(
            [
                summary is not None,
                bool(recent_games),
                bool(owned_games),
                bool(friend_list),
                bool(badges),
            ]
        )

        return {
            "available": available,
            "fetched_at": int(time.time()),
            "steamid": str(summary.get("steamid") if summary else steamid),
            "errors": errors,
            "available_fields": available_fields,
            "profile": profile,
            "status": status,
            "current_game": current_game,
            "recent_games": recent_games,
            "friends": {
                "count": len(friend_list),
                "items": friend_list[:20],
            },
            "badges": badges,
            "raw_summary": summary or {},
        }

    def format_full_player_status_text(self, payload: dict[str, Any]) -> str:
        profile = payload.get("profile", {})
        status = payload.get("status", {})
        current_game = payload.get("current_game")
        friends = payload.get("friends", {})
        badges = payload.get("badges", {})
        recent_games = payload.get("recent_games", [])
        errors = payload.get("errors", [])
        available_fields = payload.get("available_fields", {})

        lines = [
            f"SteamID: {payload.get('steamid')}",
            f"可用性: {'可用' if payload.get('available') else '部分可用/不可用'}",
            f"昵称: {profile.get('personaname') or '未知'}",
            f"主页: {profile.get('profileurl') or '未知'}",
            f"当前状态码: {status.get('personastate')}",
            f"上次离线时间戳: {status.get('lastlogoff')}",
            f"好友数量: {friends.get('count', 0)}",
            f"徽章数量: {len(badges.get('badges', []) or [])}",
        ]

        if current_game:
            lines.extend(
                [
                    f"当前游戏ID: {current_game.get('appid')}",
                    f"当前游戏名: {current_game.get('name')}",
                    f"当前游戏在线人数: {current_game.get('online_count')}",
                    f"当前游戏累计时长(小时): {current_game.get('playtime_forever_hours')}",
                ]
            )

        if recent_games:
            lines.append("最近游玩:")
            for game in recent_games[:5]:
                lines.append(
                    f"- {game.get('name', game.get('appid'))} | appid={game.get('appid')} | 最近游玩分钟={game.get('playtime_2weeks', 0)}"
                )

        lines.append("字段可用性:")
        for key, value in available_fields.items():
            lines.append(f"- {key}: {value}")

        if errors:
            lines.append("接口错误:")
            for item in errors:
                lines.append(f"- {item}")

        raw_summary = payload.get("raw_summary") or {}
        if raw_summary:
            lines.append("原始摘要字段:")
            for key, value in raw_summary.items():
                lines.append(f"- {key}: {value}")

        return "\n".join(lines)