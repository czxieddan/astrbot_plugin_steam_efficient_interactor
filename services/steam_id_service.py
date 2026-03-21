import re


class SteamIdService:
    def __init__(self, owner):
        self.owner = owner

    def _normalize_group_id(self, group_id):
        return str(group_id) if group_id is not None else "default"

    def _parse_steamids(self, steamids: str) -> list[str]:
        return [item.strip() for item in re.split(r"[,，.\s]+", steamids) if item.strip()]

    def validate_steamids(self, steamids: list[str]) -> list[str]:
        return [sid for sid in steamids if not sid.isdigit() or len(sid) != 17]

    def add_ids(self, group_id: str, steamids_text: str) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        steamid_list = self._parse_steamids(steamids_text)
        invalid_ids = self.validate_steamids(steamid_list)
        if invalid_ids:
            return {
                "ok": False,
                "group_id": normalized_group_id,
                "invalid_ids": invalid_ids,
                "added": [],
                "existed": [],
            }

        steam_id_bucket = self.owner.group_steam_ids.setdefault(normalized_group_id, [])
        added = []
        existed = []
        for sid in steamid_list:
            if sid in steam_id_bucket:
                existed.append(sid)
                continue
            if len(steam_id_bucket) >= self.owner.max_group_size:
                break
            steam_id_bucket.append(sid)
            added.append(sid)

        self.owner._save_group_steam_ids()
        self.owner._mark_dirty()
        return {
            "ok": True,
            "group_id": normalized_group_id,
            "invalid_ids": [],
            "added": added,
            "existed": existed,
            "current_ids": list(steam_id_bucket),
        }

    def remove_id(self, group_id: str, steamid: str) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        steam_id_bucket = self.owner.group_steam_ids.get(normalized_group_id, [])
        if steamid not in steam_id_bucket:
            return {
                "ok": False,
                "group_id": normalized_group_id,
                "removed": None,
                "current_ids": list(steam_id_bucket),
            }

        steam_id_bucket.remove(steamid)
        self.owner.group_steam_ids[normalized_group_id] = steam_id_bucket
        self.owner._save_group_steam_ids()
        self.owner._mark_dirty()
        return {
            "ok": True,
            "group_id": normalized_group_id,
            "removed": steamid,
            "current_ids": list(steam_id_bucket),
        }

    def list_ids(self, group_id: str) -> dict:
        normalized_group_id = self._normalize_group_id(group_id)
        steam_ids = self.owner.group_steam_ids.get(normalized_group_id, [])
        return {
            "ok": True,
            "group_id": normalized_group_id,
            "steam_ids": list(steam_ids),
            "count": len(steam_ids),
        }

    def clear_ids(self, group_id: str = "") -> dict:
        if group_id:
            normalized_group_id = self._normalize_group_id(group_id)
            self.owner.group_steam_ids[normalized_group_id] = []
            self.owner._save_group_steam_ids()
            self.owner._mark_dirty()
            return {
                "ok": True,
                "scope": "group",
                "group_id": normalized_group_id,
            }

        self.owner.group_steam_ids.clear()
        self.owner._save_group_steam_ids()
        self.owner._mark_dirty()
        return {
            "ok": True,
            "scope": "all",
            "group_id": "",
        }