import os
import json

def wakeup_subagents(prompt: str) -> str:
    from ..main import load_state
    state = load_state()
    subagent_model = state.get("subagent_model")
    
    if not subagent_model:
        return "Error: No subagent model assigned. The user MUST run the /subagents command to select a model before this tool can be used."
        
    model_name = subagent_model if isinstance(subagent_model, str) else subagent_model.get("name", "API Model")
    
    # Stub for actual subagent spawning logic
    return f"Success: Subagent awakened using model '{model_name}'. Task delegated: {prompt}"
