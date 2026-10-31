import os
import sys
import shutil
import subprocess
from typing import Dict, Any, List

def run_doctor() -> int:
    import psutil
    from .settings import get_settings
    from .resource_limits import get_dynamic_context_limit

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    settings = get_settings()
    cfg = settings.config

    print("\n  \x1b[36m⌬  CMDAI CODE — System & Provider Doctor\x1b[0m\n")
    print("━" * 60)

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)
    print(f"  {'[✔] \x1b[32m' if py_ok else '[✖] \x1b[31m'}Python Version:\x1b[0m       {py_ver} ({sys.executable})")

    git_ver = "Not installed"
    git_ok = False
    try:
        out = subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL, text=True).strip()
        git_ver = out
        git_ok = True
    except Exception:
        pass
    print(f"  {'[✔] \x1b[32m' if git_ok else '[✖] \x1b[31m'}Git Version:\x1b[0m          {git_ver}")

    vm = psutil.virtual_memory()
    total_ram_gb = vm.total / (1024**3)
    avail_ram_gb = vm.available / (1024**3)
    ram_ok = avail_ram_gb >= 2.0
    print(f"  {'[✔] \x1b[32m' if ram_ok else '[!] \x1b[33m'}System RAM:\x1b[0m           {avail_ram_gb:.1f} GB available / {total_ram_gb:.1f} GB total")

    vulkan_installed = bool(os.environ.get("VULKAN_SDK") or shutil.which("vulkan-1.dll") or os.path.exists(r"C:\VulkanSDK"))
    print(f"  {'[✔] \x1b[32m' if vulkan_installed else '[!] \x1b[33m'}Vulkan SDK:\x1b[0m           {'Detected' if vulkan_installed else 'Not detected (CPU mode will be used)'}")

    llama_ok = False
    try:
        import llama_cpp
        llama_ok = True
        llama_ver = getattr(llama_cpp, "__version__", "installed")
    except Exception:
        llama_ver = "Not installed"
    print(f"  {'[✔] \x1b[32m' if llama_ok else '[!] \x1b[33m'}llama-cpp-python:\x1b[0m     {llama_ver}")

    active_loader = cfg.get("active_loader", "cpu")
    print(f"  [i] Active Loader:         {active_loader.upper()}")

    def_prov = cfg.get("default_provider", "local_gguf")
    def_mod = cfg.get("default_model", "Qwen3-0.6B-UD-Q4_K_XL.gguf")
    dyn_limit = get_dynamic_context_limit(def_mod, def_prov)
    print(f"  [i] Default Provider:      {def_prov}")
    print(f"  [i] Default Model:         {def_mod} (L_ctx: {dyn_limit} tokens)")

    print("\n\x1b[1mAPI Providers Status:\x1b[0m")
    keys = cfg.get("keys", {})
    providers_to_check = [
        ("opencode", "OpenCode Zen"),
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic Claude"),
        ("gemini", "Google Gemini"),
        ("openrouter", "OpenRouter"),
        ("groq", "Groq"),
        ("deepseek", "DeepSeek"),
        ("ollama", "Ollama Local API"),
    ]

    for pid, pname in providers_to_check:
        key_val = keys.get(pid, "")
        if pid == "ollama":
            has_key = True
            key_status = "\x1b[32mEnabled (Local)\x1b[0m"
        elif key_val:
            masked = key_val[:4] + "..." + key_val[-3:] if len(key_val) > 8 else "***"
            key_status = f"\x1b[32mConfigured ({masked})\x1b[0m"
        else:
            key_status = "\x1b[90mNot set\x1b[0m"
        print(f"  • {pname:<20} {key_status}")

    app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sessions_dir = os.path.join(app_root, "app", "sessions")
    sess_count = 0
    if os.path.exists(sessions_dir):
        sess_count = sum(1 for f in os.listdir(sessions_dir) if f.endswith(".json"))
    print(f"\n  [i] Saved Sessions:        {sess_count} in app/sessions/")

    print("━" * 60)
    print("  \x1b[32mDoctor diagnostic complete. All core systems ready.\x1b[0m\n")
    return 0
