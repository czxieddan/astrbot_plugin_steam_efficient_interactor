from astrbot.api.message_components import Image, Plain


async def handle_openbox(self, event, steamid: str):
    """查询并格式化展示指定 SteamID 的完整聚合状态信息"""
    if not self.API_KEY:
        yield event.plain_result("未配置 Steam API Key，请先填写。")
        return

    payload = await self.steam_api_service.get_full_player_status(self.API_KEY, steamid)
    if not payload.get("available"):
        yield event.plain_result(f"未查到该 SteamID 信息：{payload.get('error', '未知错误')}")
        return

    profile = payload.get("profile", {})
    status = payload.get("status", {})
    current_game = payload.get("current_game")
    friends = payload.get("friends", {})
    badges = payload.get("badges", {})
    recent_games = payload.get("recent_games", [])

    personastate_map = {
        0: "离线",
        1: "在线",
        2: "忙碌",
        3: "离开",
        4: "打盹",
        5: "想交易",
        6: "想游戏",
    }

    lines = [
        f"SteamID: {payload.get('steamid')}",
        f"昵称: {profile.get('personaname') or '未知'}",
        f"主页链接: {profile.get('profileurl') or '未知'}",
        f"在线状态: {personastate_map.get(status.get('personastate', 0), status.get('personastate', 0))}",
        f"上次离线时间戳: {status.get('lastlogoff')}",
        f"真实姓名: {profile.get('realname') or '未知'}",
        f"账号创建时间戳: {profile.get('timecreated')}",
        f"主要群组ID: {profile.get('primaryclanid')}",
        f"社区可见性: {profile.get('communityvisibilitystate')}",
        f"资料状态: {profile.get('profilestate')}",
        f"评论权限: {profile.get('commentpermission')}",
        f"状态标志: {profile.get('personastateflags')}",
        f"位置ID: {'-'.join(str(item) for item in [profile.get('loccountrycode'), profile.get('locstatecode'), profile.get('loccityid')] if item)}",
        f"好友数量: {friends.get('count', 0)}",
        f"徽章数量: {len(badges.get('badges', []) or [])}",
    ]

    if current_game:
        lines.extend(
            [
                f"当前游戏ID: {current_game.get('appid')}",
                f"当前游戏名: {current_game.get('name')}",
                f"当前游戏在线人数: {current_game.get('online_count')}",
                f"当前游戏累计时长(分钟): {current_game.get('playtime_forever_minutes')}",
                f"当前游戏累计时长(小时): {current_game.get('playtime_forever_hours')}",
            ]
        )

    if recent_games:
        lines.append("最近游玩游戏:")
        for game in recent_games[:5]:
            lines.append(
                f"- {game.get('name', game.get('appid'))} | appid={game.get('appid')} | 最近两周时长={game.get('playtime_2weeks', 0)}分钟 | 总时长={game.get('playtime_forever', 0)}分钟"
            )

    raw_summary = payload.get("raw_summary") or {}
    if raw_summary:
        lines.append("原始摘要字段:")
        for key, value in raw_summary.items():
            lines.append(f"- {key}: {value}")

    message_chain = []
    avatar_url = profile.get("avatarfull") or profile.get("avatar")
    if avatar_url:
        message_chain.append(Image.fromURL(avatar_url, width=64, height=64))
    message_chain.append(Plain("SteamID 完整状态信息：\n" + "\n".join(lines)))
    yield event.chain_result(message_chain)