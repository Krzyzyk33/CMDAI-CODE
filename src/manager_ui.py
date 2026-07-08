import questionary
from rich.console import Console
from .model_picker import create_picker_app

console = Console()

def run_models_manager(state, save_callback):
    while True:
        api_models = state.get("api_models", [])
        if not api_models:
            console.print("[yellow]No saved API models found.[/yellow]")
            import time; time.sleep(1.5)
            return

        tabs = ["Model Manager"]
        options = []
        for i, m in enumerate(api_models):
            options.append(f"{m['name']} [{m.get('provider', 'unknown').upper()}]")
        options.append("Exit")

        res = create_picker_app(tabs, {0: options}, start_tab=0)

        if res["action"] != "select" or res["value"] == "Exit":
            break

        selected_opt = res["value"]
        selected_idx = options.index(selected_opt)
        model = api_models[selected_idx]
        
        res2 = create_picker_app([f"Editing: {model['name']}"], {0: ["Rename model", "Change Base URL", "Delete model", "Back"]})
        
        if res2["action"] != "select" or res2["value"] == "Back":
            continue
            
        action = res2["value"]

        if action == "Rename model":
            new_name = questionary.text("Enter new model name:", default=model["name"]).ask()
            if new_name:
                api_models[selected_idx]["name"] = new_name
                state["api_models"] = api_models
                save_callback(state)
                console.print("[green]Model name updated.[/green]")
        elif action == "Change Base URL":
            new_url = questionary.text("Enter new Base URL:", default=model.get("base_url", "")).ask()
            if new_url:
                api_models[selected_idx]["base_url"] = new_url
                state["api_models"] = api_models
                save_callback(state)
                console.print("[green]Base URL updated.[/green]")
        elif action == "Delete model":
            confirm = questionary.confirm("Are you sure you want to delete this model?").ask()
            if confirm:
                api_models.pop(selected_idx)
                state["api_models"] = api_models
                save_callback(state)
                console.print("[green]Model deleted from configuration.[/green]")

def run_api_keys_manager(state, save_callback):
    while True:
        api_keys = state.get("api_keys", {})
        if not api_keys:
            console.print("[yellow]No saved API keys found.[/yellow]")
            import time; time.sleep(1.5)
            return

        tabs = ["API Keys Manager"]
        keys_list = list(api_keys.keys())
        options = [f"Key for: {k.upper()}" for k in keys_list]
        options.append("Exit")

        res = create_picker_app(tabs, {0: options}, start_tab=0)

        if res["action"] != "select" or res["value"] == "Exit":
            break
            
        selected_opt = res["value"]
        selected_idx = options.index(selected_opt)
        selected_provider = keys_list[selected_idx]

        res2 = create_picker_app([f"Key for {selected_provider.upper()}"], {0: ["Change key", "Delete key", "Back"]})

        if res2["action"] != "select" or res2["value"] == "Back":
            continue
            
        action = res2["value"]

        if action == "Change key":
            new_key = questionary.text("Enter new API key:", default=api_keys[selected_provider]).ask()
            if new_key:
                api_keys[selected_provider] = new_key
                state["api_keys"] = api_keys
                save_callback(state)
                console.print(f"[green]API key updated for {selected_provider.upper()}.[/green]")
        elif action == "Delete key":
            confirm = questionary.confirm(f"Are you sure you want to delete the key for {selected_provider.upper()}?").ask()
            if confirm:
                del api_keys[selected_provider]
                state["api_keys"] = api_keys
                save_callback(state)
                console.print(f"[green]API key deleted for {selected_provider.upper()}.[/green]")
