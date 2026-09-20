import json
import os
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0.0",
    "app_name": "CMDAI CODE",
    "models_dir": "models",
    "default_model": "",
    "default_provider": "openrouter",
    "generation": {
        "temperature": 0.3,
        "top_p": 0.95,
        "max_tokens": 0,
        "repetition_penalty": 1.1,
        "reasoning_level": "Medium",
    },
    "agent": {
        "enabled": True,
        "max_iterations": 25,
        "auto_approve_readonly": True,
    },
    "server": {
        "enabled": True,
        "port": 8080,
        "host": "127.0.0.1",
    },
    "settings": {
        "confirm_exit": False,
        "auto_copy_selection": True,
        "enable_thinking_animation": True,
    },
    "api_keys": {},
    "custom_models": {},
    "installed_loaders": ["vulkan", "cpu"],
    "active_loader": "cpu",
}


class SettingsManager:

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = os.path.abspath(base_dir)
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(here)))

        self.config_path = os.path.join(self.base_dir, "config.json")
        self.cmdai_settings_path = os.path.join(self.base_dir, ".CMDAISETTINGS.json")
        self.config = self._load_config()
        self._load_cmdai_settings()

    def _load_config(self) -> Dict[str, Any]:
        path = self.config_path
        if not os.path.exists(path):
            example_path = os.path.join(self.base_dir, "config.example.json")
            if os.path.exists(example_path):
                path = example_path

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    merged = dict(DEFAULT_CONFIG)
                    merged.update(data)
                    return merged
            except Exception:
                pass
        return dict(DEFAULT_CONFIG)

    def _load_cmdai_settings(self) -> None:
        if os.path.exists(self.cmdai_settings_path):
            try:
                with open(self.cmdai_settings_path, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                    if "settings" not in self.config:
                        self.config["settings"] = {}
                    self.config["settings"].update(s_data)
                    if "server_port" in s_data:
                        self.config.setdefault("server", {})["port"] = s_data["server_port"]
                    if "default_provider" in s_data:
                        self.config["default_provider"] = s_data["default_provider"]
            except Exception:
                pass

    def save_config(self) -> bool:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)

            s_dict = dict(self.config.get("settings", {}))
            if "server" in self.config and "port" in self.config["server"]:
                s_dict["server_port"] = self.config["server"]["port"]
            if "default_provider" in self.config:
                s_dict["default_provider"] = self.config["default_provider"]

            with open(self.cmdai_settings_path, "w", encoding="utf-8") as f:
                json.dump(s_dict, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def get_api_key(self, provider_id: str) -> str:
        env_names = [
            f"{provider_id.upper()}_API_KEY",
            f"{provider_id.upper()}_KEY",
        ]
        for name in env_names:
            val = os.environ.get(name)
            if val:
                return val.strip()

        api_keys = self.config.get("api_keys", {})
        return api_keys.get(provider_id, "").strip()

    def set_api_key(self, provider_id: str, key: str) -> bool:
        if "api_keys" not in self.config:
            self.config["api_keys"] = {}
        self.config["api_keys"][provider_id] = key.strip()
        return self.save_config()

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        self.config[key] = value
        return self.save_config()

    def get_custom_models(self, provider_id: str) -> List[str]:
        c_dict = self.config.get("custom_models", {})
        if not isinstance(c_dict, dict):
            return []
        return list(c_dict.get(provider_id, []))

    def add_custom_model(self, provider_id: str, model_id: str) -> bool:
        if "custom_models" not in self.config or not isinstance(self.config["custom_models"], dict):
            self.config["custom_models"] = {}
        prov_list = self.config["custom_models"].setdefault(provider_id, [])
        clean_id = model_id.strip()
        if clean_id and clean_id not in prov_list:
            prov_list.append(clean_id)
            return self.save_config()
        return True


_instance: Optional[SettingsManager] = None


def get_settings() -> SettingsManager:
    global _instance
    if _instance is None:
        _instance = SettingsManager()
    return _instance
