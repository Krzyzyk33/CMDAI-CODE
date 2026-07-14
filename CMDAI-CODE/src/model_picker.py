import os
import glob
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Window, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style

def create_picker_app(tabs, options_dict, start_tab=0):
    current_tab = start_tab
    selected_index = {i: 0 for i in range(len(tabs))}
    result = {"action": None, "tab": None, "value": None}
    
    bindings = KeyBindings()
    
    @bindings.add("q")
    @bindings.add("c-c")
    def _(event):
        result["action"] = "cancel"
        event.app.exit()
        
    @bindings.add("left")
    def _(event):
        nonlocal current_tab
        current_tab = (current_tab - 1) % len(tabs)
        
    @bindings.add("right")
    def _(event):
        nonlocal current_tab
        current_tab = (current_tab + 1) % len(tabs)
        
    @bindings.add("up")
    def _(event):
        idx = selected_index[current_tab]
        opts = options_dict[current_tab]
        selected_index[current_tab] = (idx - 1) % len(opts)
        
    @bindings.add("down")
    def _(event):
        idx = selected_index[current_tab]
        opts = options_dict[current_tab]
        selected_index[current_tab] = (idx + 1) % len(opts)
        
    @bindings.add("enter")
    def _(event):
        result["tab"] = tabs[current_tab]
        result["value"] = options_dict[current_tab][selected_index[current_tab]]
        result["action"] = "select"
        event.app.exit()
        
    def get_text():
        lines = []
        lines.append(("", "\n"))
        
                  
        for i, t in enumerate(tabs):
            if i == current_tab:
                lines.append(("class:active_tab", f"  > {t}  "))
            else:
                lines.append(("class:inactive_tab", f"    {t}  "))
        lines.append(("", "\n\n"))
        
                 
        opts = options_dict[current_tab]
        for i, opt in enumerate(opts):
            if i == selected_index[current_tab]:
                lines.append(("class:selected", f" > {opt}\n"))
            else:
                lines.append(("class:unselected", f"   {opt}\n"))
                
        lines.append(("class:help", "\n(Strzałki: nawigacja, Enter: wybór, q: wyjście)\n"))
        return lines

    style = Style.from_dict({
        "active_tab": "fg:#00ffff bold",
        "inactive_tab": "fg:#aaaaaa",
        "selected": "fg:#00ffff bold",
        "unselected": "fg:#aaaaaa",
        "help": "fg:#666666 italic"
    })
    
    control = FormattedTextControl(get_text)
    window = Window(content=control)
    layout = Layout(HSplit([window]))
    
    app = Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True
    )
    
    app.run()
    return result

def run_model_picker(state):
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_dir = os.path.join(app_dir, "models")
    local_models = glob.glob(os.path.join(models_dir, "*.gguf"))
    local_models = [os.path.basename(m) for m in local_models]
    
    api_models = state.get("api_models", [])
    
    def get_prov(m):
        p = m.get('provider')
        if p: return p.upper()
        url = m.get('base_url', '')
        from .providers import detect_provider_by_url
        return detect_provider_by_url(url).upper()
        
    tabs = ["Settings", "Local Models", "API Models"]
    options = {
        0: ["Add model", "Add API key", "Edit API keys", "Edit models", "Exit"],
        1: local_models if local_models else ["No local models"],
        2: [f"{m['name']} [{get_prov(m)}]" for m in api_models] if api_models else ["No API models"]
    }
    
    res = create_picker_app(tabs, options, start_tab=1)
    
    out = {"action": "cancel", "value": None}
    if res["action"] == "select":
        tab = res["tab"]
        val = res["value"]
        if tab == "Settings":
            if val == "Add model":
                out["action"] = "add_model"
            elif val == "Add API key":
                out["action"] = "add_api"
            elif val == "Edit API keys":
                out["action"] = "edit_api"
            elif val == "Edit models":
                out["action"] = "edit_models"
        elif tab == "Local Models":
            if val != "No local models":
                out["action"] = "load_local"
                out["value"] = os.path.join(models_dir, val)
        elif tab == "API Models":
            if val != "No API models":
                out["action"] = "load_api"
                for m in api_models:
                    if f"{m['name']} [{get_prov(m)}]" == val:
                        out["value"] = m
                        break
    return out

def run_provider_picker(mode="api"):
    from .providers import get_all_providers
    providers = get_all_providers()
    tabs = [p.DISPLAY_NAME for p in providers]
    
    options = {}
    for i in range(len(tabs)):
        if mode == "api":
            options[i] = ["Enter API key", "Cancel"]
        else:
            options[i] = ["Enter model name", "Cancel"]
        
    res = create_picker_app(tabs, options, start_tab=0)
    out = {"action": "cancel", "provider": None}
    
    if res["action"] == "select" and res["value"] != "Cancel":
        out["action"] = "continue"
        for p in providers:
            if p.DISPLAY_NAME == res["tab"]:
                out["provider"] = p.PROVIDER_ID
                break
            
    return out
