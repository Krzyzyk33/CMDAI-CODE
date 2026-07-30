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

def todo_done(step_number: int = None, restricted_dir: str = None, **kwargs) -> str:
    try:
        path = "plan.md"
        if restricted_dir:
            path = os.path.join(restricted_dir, "plan.md")
        if not os.path.exists(path):
            return "Error: plan.md does not exist."
            
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            
        lines = content.split('\n')
        
        if step_number is None:
            for k in ["step", "step_idx", "step_index", "idx", "number", "index"]:
                if k in kwargs and kwargs[k] is not None:
                    try:
                        step_number = int(kwargs[k])
                        break
                    except (ValueError, TypeError):
                        pass
                        
        found = False
        line_text = ""
        if step_number is not None:
            step_str = str(step_number)
            for i, line in enumerate(lines):
                if re.search(r'(?:^|\s)' + step_str + r'\..*?\[ \]', line):
                    line_text = line.strip()
                    lines[i] = line.replace('[ ]', '[x]', 1)
                    found = True
                    break
        else:
            for i, line in enumerate(lines):
                if '[ ]' in line:
                    line_text = line.strip()
                    lines[i] = line.replace('[ ]', '[x]', 1)
                    found = True
                    m = re.search(r'(\d+)\.', line)
                    step_number = int(m.group(1)) if m else (i + 1)
                    break
                
        if found:
            with open(path, "w", encoding="utf-8") as f:
                f.write('\n'.join(lines))
            clean = re.sub(r'^[-\s*#]*\[x\]\s*', '', line_text)
            return f"[x] {clean}"
        else:
            if step_number is not None:
                return f"Could not find uncompleted step {step_number} with '[ ]' in plan.md"
            return "No uncompleted steps with '[ ]' found in plan.md"
    except Exception as e:
        return f"Error: {e}"

def submit_plan(**kwargs) -> str:
    architecture_details = (
        kwargs.get("architecture_details")
        or kwargs.get("architecture")
        or kwargs.get("details")
        or kwargs.get("description")
        or "No architecture details provided."
    )
    steps_list = (
        kwargs.get("steps_list")
        or kwargs.get("steps")
        or kwargs.get("plan")
        or kwargs.get("step_list")
        or kwargs.get("items")
    )
    
    if not steps_list:
        if architecture_details and architecture_details != "No architecture details provided.":
            steps_list = [
                line.strip()
                for line in str(architecture_details).split('\n')
                if line.strip().startswith(('- ', '* ', '1.', '1)', '2.', '3.', '4.', '5.'))
            ]
        if not steps_list:
            steps_list = ["Execute planned tasks step by step."]
        
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
