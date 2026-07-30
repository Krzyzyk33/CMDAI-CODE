import os
import json
from typing import Dict, Any

def detect_test_framework(cwd: str) -> str:
    """Wykrywa dostępny framework testowy na podstawie plików."""
    if os.path.exists(os.path.join(cwd, "pytest.ini")) or os.path.exists(os.path.join(cwd, "setup.py")):
        return "pytest"
    if os.path.exists(os.path.join(cwd, "package.json")):
        return "npm test"
    if os.path.exists(os.path.join(cwd, "go.mod")):
        return "go test"
    if os.path.exists(os.path.join(cwd, "Cargo.toml")):
        return "cargo test"
    return ""

def update_project_config(cwd: str) -> Dict[str, Any]:
    """Aktualizuje lub tworzy project_config.json dla pętli testów."""
    proj_dir = os.path.join(cwd, ".cmdai_code_project")
    os.makedirs(proj_dir, exist_ok=True)
    
    config_path = os.path.join(proj_dir, "project_config.json")
    
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass
            
    framework = detect_test_framework(cwd)
    config["test_framework"] = framework
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        
    return config

def get_project_config(cwd: str) -> Dict[str, Any]:
    """Zwraca obecną konfigurację bez aktualizowania (szybki odczyt)."""
    config_path = os.path.join(cwd, ".cmdai_code_project", "project_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}
