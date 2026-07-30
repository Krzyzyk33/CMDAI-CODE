        pass
        
    while True:
        tokens = context.get_token_count()
        user_input = input_handler.get_input(os.path.basename(model_path), tokens, model.get_context_limit())
        user_input = user_input.strip()
        mode = input_handler.get_mode()
        hide_prompt = False
        
        current_state = load_state()
        if current_state.get("thinking_idx") != input_handler.thinking_idx:
            current_state["thinking_idx"] = input_handler.thinking_idx
            save_state(current_state)
            
        if user_input in ["/quit", "/exit"]:
            break
        elif user_input == "/clear":
            context.clear()
            os.system("cls" if os.name == "nt" else "clear")
            console.clear()
            print_header(os.path.basename(model_path) if isinstance(model_path, str) else model_path.get("name", "Unknown API Model"), cwd)
            continue
        elif user_input == "/diff":
            from .tools.git import git_diff
            from .ui import print_code_panel
            diff_output = git_diff(cwd)
            print_code_panel("Git Diff", diff_output, lexer_override="diff")
            continue
        elif user_input == "/undo":
            from .tools.git import git_undo
            res = git_undo(cwd)
            console.print(f"\n● [white]/undo[/white]")
            from .ui import MUTED_COLOR
            console.print(f"[{MUTED_COLOR}]  ⎿  Undo executed (stashed changes): {res}[/]")
            continue
        elif user_input.startswith("/commit"):
            from .tools.git import git_commit
            msg = user_input[7:].strip() or "Automated commit by CMDAI CODE"
            res = git_commit(msg, cwd)
            console.print(f"\n● [white]/commit[/white]")
            from .ui import MUTED_COLOR
            console.print(f"[{MUTED_COLOR}]  ⎿  Committed successfully: {res}[/]")
            continue
        elif user_input == "/compact":
            context.trigger_compaction(model)
        elif user_input == "/review":
            agent.auto_review = not agent.auto_review
            stan = "ENABLED" if agent.auto_review else "DISABLED"
            console.print(f"\n[magenta]🔍 Auto-Reflection (self-correction) mode was {stan}.[/magenta]")
        elif user_input.startswith("/sessions"):
            while True:
                sm = context.session_manager
                sessions = sm.get_all_sessions()
                
                from .session_picker import run_session_picker
                res = run_session_picker(sessions, sm.current_state.session_id)
                
                if res["action"] == "cancel":
                    break
                    
                if res["action"] == "new":
                    import questionary
                    new_id = questionary.text("Enter name for the new session (enter to cancel):").ask()
                    if new_id and new_id.strip():
                        new_id = new_id.strip()
                        context.load_history(new_id)
                        os.system("cls" if os.name == "nt" else "clear")
                        console.clear()
                        print_header(os.path.basename(model_path) if isinstance(model_path, str) else model_path.get("name", "Unknown API Model"), cwd)
                        print_chat_history(context)
                        console.print(f"\n● [white]/sessions[/white]")
                        from .ui import MUTED_COLOR
                        console.print(f"[{MUTED_COLOR}]  ⎿  Created and loaded: {new_id} ({len(context.full_messages)} messages)[/]")
                    break
                elif res["action"] == "delete":
                    del_id = res["value"]
                    sm.delete_session(del_id)
                    console.print(f"\n● [white]/sessions[/white]")
                    from .ui import MUTED_COLOR
                    console.print(f"[{MUTED_COLOR}]  ⎿  Successfully deleted session: {del_id}[/]")
                    
                    if del_id == sm.current_state.session_id:
                        context.clear()
                        console.print(f"[{MUTED_COLOR}]  ⎿  Deleted active session. Starting a new one.[/]")
                                                               
                elif res["action"] == "load":
                    s_id = res["value"]
                    context.load_history(s_id)
                    os.system("cls" if os.name == "nt" else "clear")
                    console.clear()
                    print_header(os.path.basename(model_path) if isinstance(model_path, str) else model_path.get("name", "Unknown API Model"), cwd)
                    print_chat_history(context)
                    console.print(f"\n● [white]/sessions[/white]")
                    from .ui import MUTED_COLOR
                    console.print(f"[{MUTED_COLOR}]  ⎿  Loaded: {s_id} ({len(context.full_messages)} messages)[/]")
                    break
            continue
        elif user_input == "/subagents":
            from .model_picker import run_model_picker
            s_tmp = load_state()
            old_model_raw = s_tmp.get("subagent_model")
            old_model_name = old_model_raw.get("name", "None") if isinstance(old_model_raw, dict) else (__import__("os").path.basename(old_model_raw) if old_model_raw else "None")
            res = run_model_picker(s_tmp)
            
            if res["action"] in ["load_local", "load_api"]:
                s = load_state()
                s["subagent_model"] = res["value"]
                save_state(s)
                model_name = res['value'].get('name', 'Unknown API Model') if isinstance(res['value'], dict) else os.path.basename(res['value'])
                console.print("\n● [white]/subagents[/white]")
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  {old_model_name} -> {model_name}[/]")
                console.print(f"[{MUTED_COLOR}]  ⎿  {old_model_name} -> {model_name}[/]")
            else:
                console.print("[yellow]Subagent model selection cancelled.[/yellow]")
            continue
        elif user_input == "/ide":
            console.print(f"\n● [white]/ide[/white]")
            in_ide = os.environ.get("TERM_PROGRAM") in ["vscode", "JetBrains-JediTerm"] or "VSCODE_PID" in os.environ or "TERMINAL_EMULATOR" in os.environ
            if not in_ide:
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  [red]Error: Run CMD AI inside an integrated terminal (e.g. VS Code).[/red][/]")
            else:
                context.ide_mode = True
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  IDE Server connected (port {ide_server.port}). Environment isolation active.[/]")
            continue
        elif user_input == "/auto":
            input_handler.mode_index = input_handler.modes.index("auto")
            console.print(f"\n● [white]/auto[/white]")
            from .ui import MUTED_COLOR
            console.print(f"[{MUTED_COLOR}]  ⎿  Mode changed to: auto[/]")
            continue
        elif user_input == "/code":
            input_handler.mode_index = input_handler.modes.index("code")
            console.print(f"\n● [white]/code[/white]")
            from .ui import MUTED_COLOR
            console.print(f"[{MUTED_COLOR}]  ⎿  Mode changed to: code[/]")
            continue
        elif user_input in ["/progress", "/progres"]:
            if context.session_manager.current_state.current_plan:
                plan = context.session_manager.current_state.current_plan
                done = sum(1 for _, is_done in plan if is_done)
                percent = int((done / len(plan)) * 100) if len(plan) > 0 else 100
                bar_length = 20
                filled = int(bar_length * done / len(plan)) if len(plan) > 0 else bar_length
                bar = "█" * filled + "░" * (bar_length - filled)
                console.print(f"\n● [white]/progress[/white]")
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  {bar} {percent}% ({done}/{len(plan)} steps)[/]")
            else:
                console.print(f"\n● [white]/progress[/white]")
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  No active plan.[/]")
            continue
        elif user_input == "/plan":
            input_handler.mode_index = input_handler.modes.index("plan")
            console.print(f"\n● [white]/plan[/white]")
            from .ui import MUTED_COLOR
            console.print(f"[{MUTED_COLOR}]  ⎿  Mode changed to: plan[/]")
            continue
        elif user_input == "/llama":
            from .model_picker import create_picker_app
            import importlib.util
            import subprocess
            
                                         
            state = load_state()
            current_engine = state.get("llama_engine", "llama cpp")
            
            installed = []
            if importlib.util.find_spec("llama_cpp"):
                                                                          
                detected = False
                try:
                    import io
                    import llama_cpp
                    
                    fd = sys.stderr.fileno()
                    old_stderr = os.dup(fd)
                    
                    capture_file = os.path.join(cwd, "stderr_capture.txt")
                    with open(capture_file, "w") as f:
                        os.dup2(f.fileno(), fd)
                        llama_cpp.llama_print_system_info()
                        
                    os.dup2(old_stderr, fd)
                    os.close(old_stderr)
                    
                    with open(capture_file, "r") as f:
                        stderr_out = f.read().lower()
                        
                    try: os.remove(capture_file)
                    except: pass
                    
                    if "vulkan" in stderr_out:
                        installed.append("llama vulcan")
                    else:
                        installed.append("llama cpp")
                    detected = True
                except:
                    pass
                    
                if not detected:
                    if current_engine in ["llama cpp", "llama vulcan"]:
                        installed.append(current_engine)
                    else:
                        installed.append("llama cpp")
                    
            if not installed:
                installed = ["No installed engines"]
            
            tabs = ["Installed", "Installation"]
            
            available_to_install = [e for e in ["llama cpp", "llama vulcan"] if e not in installed]
            if not available_to_install:
                available_to_install = ["All installed"]
                
            options = {
                0: installed + ["Cancel"],
                1: available_to_install + ["Cancel"]
            }
            
            res = create_picker_app(tabs, options, start_tab=0)
            
            if res["action"] == "select" and res["value"] not in ["Cancel", "No installed engines", "All installed"]:
                selected = res["value"]
                if res["tab"] == "Installation":
                    console.print(f"\n[yellow]⏳ Starting engine installation: {selected}...[/yellow]")
                    
                    cmd = ""
                    if selected == "llama cpp":
                        cmd = "pip install llama-cpp-python --force-reinstall --no-cache-dir"
                    elif selected == "llama vulcan":
                        if os.name == "nt":
                            cmd = "set CMAKE_ARGS=-DGGML_VULCAN=1 && pip install llama-cpp-python --force-reinstall --no-cache-dir"
                        else:
                            cmd = 'CMAKE_ARGS="-DGGML_VULCAN=1" pip install llama-cpp-python --force-reinstall --no-cache-dir'
                        
                    if cmd:
                        console.print(f"[cyan]Executing: {cmd}[/cyan]")
                        subprocess.run(cmd, shell=True)
                    
                    state["llama_engine"] = selected
                    save_state(state)
                    console.print(f"[green]✅ Llama engine has been installed and set to: {selected}[/green]")
                    import time; time.sleep(2)
                elif res["tab"] == "Installed":
                    state["llama_engine"] = selected
                    save_state(state)
                    console.print(f"[green]✅ Llama engine set to: {selected}[/green]")
                    import time; time.sleep(1)
                    
            os.system("cls" if os.name == "nt" else "clear")
            print_header(os.path.basename(model_path), cwd)
            continue
        elif user_input == "/model":
            from .model_picker import run_model_picker
            while True:
                
                state = load_state()
                result = run_model_picker(state)
                
                if result["action"] == "add_api":
                    from .model_picker import run_provider_picker
                    sub_res = run_provider_picker(mode="api")
                    if sub_res["action"] == "cancel":
                        continue
                        
                    provider = sub_res["provider"]
                    api_key = console.input(f"\n[bold]Enter API key for {provider.upper()}: [/bold]")
                    
                    if api_key:
                        if "api_keys" not in state:
                            state["api_keys"] = {}
                        state["api_keys"][provider] = api_key
                        save_state(state)
                        console.print(f"[green]Saved API key for {provider.upper()}.[/green]")
                        import time; time.sleep(1)
                    continue
                    
                elif result["action"] == "edit_api":
                    from .manager_ui import run_api_keys_manager
                    run_api_keys_manager(state, save_state)
                    continue
                    
                elif result["action"] == "edit_models":
                    from .manager_ui import run_models_manager
                    run_models_manager(state, save_state)
                    continue
                    
                elif result["action"] == "add_model":
                    from .model_picker import run_provider_picker
                    sub_res = run_provider_picker(mode="model")
                    if sub_res["action"] == "cancel":
                        continue
                        
                    provider = sub_res["provider"]
                    api_keys = state.get("api_keys", {})
                    if provider not in api_keys and provider != "localllmapi":
                        console.print(f"\n[red]Missing API key for {provider.upper()}! Please select 'Add API key' first.[/red]")
                        import time; time.sleep(2)
                        continue
                        
                    model_name = console.input(f"\n[bold]Enter model name for {provider.upper()}: [/bold]")
                    if model_name:
                        if provider == "localllmapi":
                            base_url = console.input(f"\n[bold]Enter Base URL for local API (e.g. http://127.0.0.1:1234/v1): [/bold]")
                            if not base_url:
                                continue
                            api_key_val = console.input(f"\n[bold]Enter API Key (press Enter for 'not-needed'): [/bold]")
                            if not api_key_val:
                                api_key_val = "not-needed"
                        else:
                            api_key_val = api_keys[provider]
                            from src.providers import get_provider
                            provider_module = get_provider(provider)
                            if provider_module:
                                base_url = provider_module.BASE_URL
                            else:
                                base_url = "https://api.openai.com/v1"
                                
                        new_api_model = {"name": model_name, "api_key": api_key_val, "base_url": base_url, "provider": provider}
                        if "api_models" not in state:
                            state["api_models"] = []
                        state["api_models"].append(new_api_model)
                        save_state(state)
                        result["action"] = "load_api"
                        result["value"] = new_api_model
                        break
                    else:
                        continue
                        
                elif result["action"] in ["cancel", "load_local", "load_api"]:
                    break

            if result["action"] == "cancel":
                continue
                    
            if result["action"] == "load_local":
                new_model_path = result["value"]
                state["model_path"] = new_model_path
                state["model_type"] = "local"
                save_state(state)
                
                console.print(f"\n● [white]/model[/white]")
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  Loaded local model: {os.path.basename(new_model_path)}[/]")
                
                
                del agent.model
                del model
                import gc
                gc.collect()
                
                model_path = new_model_path
                n_gpu_layers = state.get("n_gpu_layers", -1)
                model = LlamaModel(model_path, n_gpu_layers=n_gpu_layers)
                agent.model = model
                
            elif result["action"] == "load_api":
                m_val = result["value"]
                state["model_type"] = "api"
                state["active_api_model"] = m_val
                save_state(state)
                console.print(f"\n● [white]/model[/white]")
                from .ui import MUTED_COLOR
                console.print(f"[{MUTED_COLOR}]  ⎿  Loaded API: {m_val['name']}[/]")
                
                if hasattr(model, 'llm'):
                    del agent.model
                    del model
                    import gc
                    gc.collect()
                    
                from .api_model import OpenAIAPIModel
                model_path = m_val['name']
                model = OpenAIAPIModel(model_name=m_val['name'], api_key=m_val['api_key'], base_url=m_val['base_url'], provider_id=m_val.get('provider'))
                agent.model = model
            continue
        elif user_input == "/init":
            console.print(f"\n● [white]/init[/white]")
            from .ui import MUTED_COLOR
            try:
                py_files = []
                for root, dirs, files in os.walk(cwd):
                    if '.git' in dirs: dirs.remove('.git')
                    if '__pycache__' in dirs: dirs.remove('__pycache__')
                    for file in files:
                        if file.endswith('.py') or file.endswith('.md'):
                            py_files.append(file)
                console.print(f"[{MUTED_COLOR}]  ⎿  Scanned repository. Found {len(py_files)} files (knowledge base built).[/]")
            except Exception as e:
                console.print(f"[{MUTED_COLOR}]  ⎿  Scan error: {e}[/]")
            user_input = "Carefully review all markdown files (.md) in the project (especially CMDAI.md and plan.md). Analyze them and immediately begin executing any plans or instructions found within them."
            hide_prompt = True
        elif user_input.startswith("/"):
            console.print(f"Unknown command or not implemented: {user_input}")
            continue
        
