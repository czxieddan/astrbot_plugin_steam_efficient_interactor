import os
from copy import deepcopy

from .runtime_utils import load_json_file, save_json_file


class SteamConfigService:
    PERSISTENT_KEYS = {
        "steam_api_key",
        "steam_api_keys",
        "sgdb_api_key",
        "fixed_poll_interval",
        "retry_times",
        "detailed_poll_log",
        "steam_group_mapping",
        "enable_failure_blacklist",
        "max_achievement_notifications",
    }

    PRIORITY_NON_EMPTY_KEYS = {
        "steam_api_key",
        "steam_api_keys",
        "sgdb_api_key",
    }

    def __init__(self, owner):
        self.owner = owner
        self.default_config_path = os.path.join(os.path.dirname(__file__), "config.json")
        self.runtime_config_path = os.path.join(owner.data_dir, "runtime_config.json")

    def load_default_config(self) -> dict:
        return load_json_file(self.default_config_path, {})

    def load_runtime_config(self) -> dict:
        return load_json_file(self.runtime_config_path, {})

    def normalize_incoming_config(self, incoming_config) -> dict:
        if incoming_config is None:
            return {}

        if isinstance(incoming_config, dict):
            return dict(incoming_config)

        if hasattr(incoming_config, "items"):
            try:
                return dict(incoming_config.items())
            except Exception:
                pass

        if hasattr(incoming_config, "to_dict"):
            try:
                data = incoming_config.to_dict()
                if isinstance(data, dict):
                    return dict(data)
            except Exception:
                pass

        if hasattr(incoming_config, "dict"):
            try:
                data = incoming_config.dict()
                if isinstance(data, dict):
                    return dict(data)
            except Exception:
                pass

        if hasattr(incoming_config, "data"):
            data = getattr(incoming_config, "data")
            if isinstance(data, dict):
                return dict(data)
            if hasattr(data, "items"):
                try:
                    return dict(data.items())
                except Exception:
                    pass

        normalized = {}
        for key in dir(incoming_config):
            if key.startswith("_"):
                continue
            try:
                value = getattr(incoming_config, key)
            except Exception:
                continue
            if callable(value):
                continue
            normalized[key] = value
        return normalized

    def _is_non_empty_value(self, value) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return any(str(item).strip() for item in value)
        return True

    def _merge_with_priority(self, default_config: dict, runtime_config: dict, incoming_config: dict) -> dict:
        merged = deepcopy(default_config)
        merged.update(runtime_config)

        for key, value in incoming_config.items():
            if key in self.PRIORITY_NON_EMPTY_KEYS:
                if self._is_non_empty_value(value):
                    merged[key] = value
                continue
            merged[key] = value

        return merged

    def mask_secret(self, value) -> str:
        if isinstance(value, list):
            parts = [self.mask_secret(item) for item in value if str(item).strip()]
            return ", ".join(parts) if parts else "(empty)"
        text = str(value or "").strip()
        if not text:
            return "(empty)"
        if len(text) <= 8:
            return "*" * len(text)
        return f"{text[:4]}...{text[-4:]}"

    def load_merged_config(self, incoming_config=None) -> dict:
        default_config = self.load_default_config()
        persisted_config = self.load_runtime_config()
        normalized_incoming = self.normalize_incoming_config(incoming_config)
        merged_config = self._merge_with_priority(default_config, persisted_config, normalized_incoming)
        self.save_runtime_config(merged_config)
        return merged_config

    def save_runtime_config(self, config: dict):
        runtime_payload = {}
        for key, value in config.items():
            if key in self.PERSISTENT_KEYS:
                runtime_payload[key] = value
        save_json_file(self.runtime_config_path, runtime_payload)

    def set_value(self, config: dict, key: str, value):
        config[key] = value
        self.save_runtime_config(config)
        return {
            "ok": True,
            "key": key,
            "value": value,
        }