import time


class SteamMonitorService:
    def __init__(self, owner):
        self.owner = owner

    def _normalize_group_id(self, group_id):
        return str(group_id) if group_id is not None else "default"

    async def enable_monitor(self, group_id: str, unified_session: str | None = None) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        steam_ids = self.owner.group_steam_ids.get(normalized_group_id, [])
        if not self.owner.API_KEY:
            return {
                "ok": False,
                "reason": "missing_api_key",
                "group_id": normalized_group_id,
            }
        if not steam_ids:
            return {
                "ok": False,
                "reason": "missing_steamids",
                "group_id": normalized_group_id,
            }

        self.owner.group_monitor_enabled[normalized_group_id] = True
        self.owner.running_groups.add(normalized_group_id)
        if unified_session:
            self.owner.notify_sessions[normalized_group_id] = unified_session
            self.owner._save_notify_session()

        status_map = await self.owner._fetch_player_status_batch(steam_ids, retry=1)
        now = int(time.time())
        for sid, status in status_map.items():
            self.owner.group_last_states.setdefault(normalized_group_id, {})[sid] = status
            if status.get("gameid"):
                self.owner.group_start_play_times.setdefault(normalized_group_id, {}).setdefault(sid, {})
                self.owner.group_start_play_times[normalized_group_id][sid][str(status["gameid"])] = now
            self.owner._update_next_poll_time(normalized_group_id, sid, status)
        self.owner._mark_dirty()

        return {
            "ok": True,
            "group_id": normalized_group_id,
            "count": len(steam_ids),
        }

    def disable_monitor(self, group_id: str) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        self.owner.group_monitor_enabled[normalized_group_id] = False
        self.owner.running_groups.discard(normalized_group_id)
        return {
            "ok": True,
            "group_id": normalized_group_id,
        }

    def enable_achievement(self, group_id: str) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        self.owner.group_achievement_enabled[normalized_group_id] = True
        return {
            "ok": True,
            "group_id": normalized_group_id,
        }

    def disable_achievement(self, group_id: str) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        self.owner.group_achievement_enabled[normalized_group_id] = False
        return {
            "ok": True,
            "group_id": normalized_group_id,
        }

    def reset_runtime_state(self) -> dict:
        self.owner.group_last_states.clear()
        self.owner.group_start_play_times.clear()
        self.owner.group_last_quit_times.clear()
        self.owner.group_pending_logs.clear()
        self.owner.group_pending_quit.clear()
        self.owner.group_recent_games.clear()
        self.owner.next_poll_time.clear()
        self.owner._superpower_cache.clear()
        self.owner._game_name_cache.clear()
        self.owner._online_count_cache.clear()
        self.owner.achievement_snapshots.clear()
        for task in self.owner.achievement_poll_tasks.values():
            task.cancel()
        self.owner.achievement_poll_tasks.clear()
        self.owner._mark_dirty()
        return {
            "ok": True,
        }