import os
import re
import json
import glob as pyglob
import subprocess
import shutil
from typing import List, Dict, Any, Optional

def save_plan(content: str, restricted_dir: str = None, **kwargs) -> str:
    try:
        path = "plan.md"
        if restricted_dir:
            path = os.path.join(restricted_dir, "plan.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return "Plan successfully saved to plan.md"
    except Exception as e:
        return f"Error: {e}"

def mark_plan_step_done(step_number: int, restricted_dir: str = None, **kwargs) -> str:
    try:
        path = "plan.md"
        if restricted_dir:
            path = os.path.join(restricted_dir, "plan.md")
        if not os.path.exists(path):
            return "Error: plan.md does not exist."
            
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            
        import re
        lines = content.split('\n')
        step_str = str(step_number)
        
        found = False
        for i, line in enumerate(lines):
                                                                   
            if re.search(r'(?:^|\s)' + step_str + r'\..*?\[ \]', line):
                lines[i] = line.replace('[ ]', '[x]', 1)
                found = True
                break
                
        if found:
            with open(path, "w", encoding="utf-8") as f:
                f.write('\n'.join(lines))
            return f"Step {step_number} marked as done in plan.md"
        else:
            return f"Could not find uncompleted step {step_number} with '[ ]' in plan.md"
    except Exception as e:
        return f"Error: {e}"

def submit_plan(**kwargs) -> str:
    architecture_details = kwargs.get("architecture_details", "No architecture details provided.")
    steps_list = kwargs.get("steps_list")
    
    if not steps_list:
        if architecture_details and architecture_details != "No architecture details provided.":
            steps_list = [line.strip() for line in architecture_details.split('\n') if line.strip().startswith(('- ', '* ', '1.', '1)'))]
        if not steps_list:
            return "Error: You MUST provide 'steps_list' argument as an array of strings (e.g. ['Step 1', 'Step 2'])."
        
    if isinstance(steps_list, str):
        try:
            steps_list = json.loads(steps_list)
            if not isinstance(steps_list, list):
                steps_list = [steps_list]
        except (json.JSONDecodeError, TypeError):
            steps_list = [s.strip() for s in steps_list.split('\n') if s.strip()]
        
    content = f"# Architecture Details\n{architecture_details}\n\n## Steps\n"
    for i, step in enumerate(steps_list):
        content += f"{i+1}. [ ] {step}\n"
    return save_plan(content, **kwargs)

def todo_write(items: List[str]) -> str:
                                               
    return "Todo list updated."

