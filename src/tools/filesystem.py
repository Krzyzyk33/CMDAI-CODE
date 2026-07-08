import os
import re
import glob as pyglob
import subprocess
import shutil
from typing import List, Dict, Any, Optional

def read_file(path: str, offset: int = 0, limit: Optional[int] = None) -> str:
    if not os.path.exists(path):
        return f"Error: File {path} not found."
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        end = offset + limit if limit else len(lines)
        subset = lines[offset:end]
        return "".join(subset)

def create_file(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Success: File {path} created/overwritten."

def edit_file(path: str, old_str: str, new_str: str) -> str:
    if not os.path.exists(path):
        return f"Error: File {path} not found."
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    count = content.count(old_str)
    if count == 0:
        return "Error: old_str not found in file."
    elif count > 1:
        return "Error: old_str is ambiguous, found multiple times. Provide more context."
        
    new_content = content.replace(old_str, new_str)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return f"Success: Replaced old_str with new_str in {path}."

def replace_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
    if not os.path.exists(path):
        return f"Error: File {path} not found."
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if start_line < 1 or start_line > len(lines):
        return f"Error: start_line {start_line} is out of bounds."
    if end_line < start_line or end_line > len(lines):
        return f"Error: end_line {end_line} is out of bounds."
    
                                      
    lines[start_line - 1:end_line] = [new_content + ("\n" if not new_content.endswith("\n") else "")]
    
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return f"Success: Replaced lines {start_line}-{end_line} in {path}."

def append_file(path: str, content: str) -> str:
    if not os.path.exists(path):
        return f"Error: File {path} not found."
    with open(path, "a", encoding="utf-8") as f:
        if not content.startswith("\n"):
            f.write("\n")
        f.write(content)
    return f"Success: Appended content to {path}."

def write_file(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Success: File {path} overwritten."

def delete_file(path: str) -> str:
    if not os.path.exists(path):
        return f"Error: Path {path} not found."
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    return f"Success: {path} deleted."

def _has_syntax_error(target_path: str) -> bool:
    import ast
    import os
    if os.path.isfile(target_path):
        if target_path.endswith('.py'):
            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    ast.parse(f.read(), filename=target_path)
            except Exception:
                return True
        return False
    
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if d not in ['.git', 'venv', 'env', '__pycache__', 'node_modules', '.pytest_cache']]
        for file in files:
            if file.endswith('.py'):
                try:
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        ast.parse(f.read(), filename=os.path.join(root, file))
                except Exception:
                    return True
    return False

def run_ls(path: str = ".") -> str:
    if not path or not str(path).strip():
        path = "."
    if not os.path.exists(path):
        return f"Error: Path {path} not found."
    if os.path.isfile(path):
        return path
        
    try: 
        entries = [e for e in os.listdir(path) if e != ".cmdai_code_project" and not e.startswith("__pycache__")]
    except Exception: 
        return "Error: Access Denied or Invalid Directory"
        
    if not entries:
        return "Empty directory."
        
    entries.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
    
    result = [f"|_ {os.path.basename(os.path.abspath(path)) or path}"]
    limit = 200
    
    for entry in entries[:limit]:
        full_path = os.path.join(path, entry)
        prefix = "[red]●[/red] " if _has_syntax_error(full_path) else ""
        
        if os.path.isdir(full_path):
            try:
                sub_count = len([e for e in os.listdir(full_path) if e != ".cmdai_code_project"])
                info = f" ({sub_count} items)"
            except Exception: 
                info = ""
            result.append(f"   |_ {prefix}{entry}/{info}")
        else:
            result.append(f"   |_ {prefix}{entry}")
            
    if len(entries) > limit:
        result.append(f"   |_ and {len(entries) - limit} files")
        
    return "\n".join(result)

def run_glob(pattern: str) -> str:
    import glob as pyglob
    
                                        
    if pattern == "*":
        pattern = "**/*"
    elif pattern.startswith("*.") and not pattern.startswith("**/*"):
        pattern = "**/" + pattern
    elif "*" not in pattern and "." not in pattern:
        pattern = pattern.rstrip("/") + "/**/*"
    elif pattern.endswith("**"):
        pattern = pattern + "/*"
        
    matches = pyglob.glob(pattern, recursive=True)
    if not matches:
        return "No files found matching pattern."
        
    res = []
    limit = 5000
    for m in matches:
        if not os.path.isfile(m):
            continue
            
        m_norm = m.replace("\\", "/")
        if any(ignored in m_norm.split("/") for ignored in [".cmdai_code_project", "venv", "node_modules", ".git", "__pycache__"]):
            continue
            
        prefix = "[red]●[/red] " if _has_syntax_error(m) else ""
        res.append(f"{prefix}{m}")
        
        if len(res) >= limit:
            break
            
    if not res:
        return "No valid files found matching pattern (only directories or ignored files)."
        
    if len(matches) > limit:
        res.append(f"...and more files omitted.")
    return "\n".join(res)

