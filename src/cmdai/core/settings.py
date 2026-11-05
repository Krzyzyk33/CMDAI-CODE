import copy
import json
import os
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0.0",
    "app_name": "CMDAI CODE",
    "models_dir": "models",
    "default_model": "",
    "default_provider": "openrouter",
    # A subagent may run on a different model than the chat it was spawned
    # from. Empty means "the same one".
    #
    # This exists because inheriting is not always safe. A subagent inherits the
    # chat's provider, and when that provider is a local bridge the subagent
    # gets a tool loop instead of a report - the window opens on an empty card
    # with the task at the bottom and nothing above it, and nothing in the
    # transcript says why.
    "subagent_provider": "",
    "subagent_model": "",
    "generation": {
        "temperature": 0.3,
        "top_p": 0.95,
        "max_tokens": 0,
        "repetition_penalty": 1.1,
        # A remembered effort level, not a switch. Whether the model reasons at
        # all is detected per model, so there is no "off" stored here - an
        # empty value just means "use whatever the model defaults to".
        "reasoning_level": "",
    },
    "agent": {
        "enabled": True,
        "max_iterations": 25,
        "auto_approve_readonly": True,
    },
    "permissions": {
        "read": "allow",
        "ls": "allow",
        "glob": "allow",
        "search": "allow",
        "write": "ask",
        "edit": "ask",
        "command": {
            "git status*": "allow",
            "git diff*": "allow",
            "git log*": "allow",
            "*": "ask",
        },
    },
    "server": {
        "enabled": True,
        "port": 8080,
        "host": "127.0.0.1",
    },
    "updates": {
        # `auto_update` is a git pull the user did not ask for, so it stays off
        # until they turn it on. `check_on_start` only reports; it never pulls.
        "auto_update": False,
        "check_on_start": True,
        "interval_hours": 24,
        "last_check": 0,
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
                # Deep copy, not dict(): the defaults hold nested blocks
                # (server, generation, updates), and a shallow copy would hand
                # every SettingsManager the same dict objects. Saving one
                # setting would then leak into DEFAULT_CONFIG and into every
                # other manager built later in the process.
                merged = copy.deepcopy(DEFAULT_CONFIG)
                merged.update(data)
                return merged
            except Exception:
                pass
        return copy.deepcopy(DEFAULT_CONFIG)

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

    def get_custom_providers(self) -> Dict[str, Dict[str, Any]]:
        c_provs = self.config.get("custom_providers", {})
        if not isinstance(c_provs, dict):
            return {}
        return dict(c_provs)

    def add_custom_provider(
        self,
        name: str,
        base_url: str,
        api_key: str = "",
        default_model: str = "",
        provider_id: str = "",
    ) -> str:
        if "custom_providers" not in self.config or not isinstance(self.config["custom_providers"], dict):
            self.config["custom_providers"] = {}

        name_clean = name.strip()
        if not provider_id:
            provider_id = re.sub(r'[^a-z0-9_]+', '_', name_clean.lower()).strip('_')
            if not provider_id:
                provider_id = f"custom_{int(time.time())}"

        url_clean = base_url.strip().rstrip("/")
        models = [default_model.strip()] if default_model.strip() else []

        self.config["custom_providers"][provider_id] = {
            "id": provider_id,
            "name": name_clean,
            "base_url": url_clean,
            "default_models": models,
        }
        if api_key.strip():
            self.set_api_key(provider_id, api_key.strip())
        if default_model.strip():
            self.add_custom_model(provider_id, default_model.strip())
        self.save_config()
        return provider_id


_instance: Optional[SettingsManager] = None


def get_settings() -> SettingsManager:
    global _instance
    if _instance is None:
        _instance = SettingsManager()
    return _instance
