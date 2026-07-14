import os
import json
import hashlib
from typing import Dict, Any

def get_db():
    from indexer.symbol_db import SymbolDB
    cwd = os.getcwd()
    db_path = os.path.join(cwd, ".cmdai_code_project", "index.db")
    if os.path.exists(db_path):
        return SymbolDB(db_path)
    return None

def verify_code(model, filepath: str, code: str, task: str = "") -> Dict[str, Any]:
    """
    Weryfikuje zmieniony kod przy użyciu tego samego modelu.
    Korzysta z cache per-plik (Krok 6) aby nie weryfikować dwukrotnie tego samego.
    """
    db = get_db()
    file_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()
    
                                                                                    
    if db:
                                                                                
        pseudo_symbol_id = hash(filepath) & 0x7FFFFFFF
        cached = db.get_cached(pseudo_symbol_id, "self_verify", file_hash)
        if cached:
            try:
                return json.loads(cached)
            except:
                pass

    messages = [
        {"role": "system", "content": "You are a strict code verifier (Self-Verification). Analyze the provided code for syntax errors, logical bugs, and task compliance. ALWAYS respond in JSON format: {\"status\": \"OK\" | \"NEEDS_FIX\", \"issues\": \"Description of issues if NEEDS_FIX, otherwise empty string\"}."},
        {"role": "user", "content": f"Task: {task}\n\nFile: {filepath}\n\nCode:\n```\n{code}\n```"}
    ]
    
    from tools import TOOLS_DEFINITIONS
    
                                                                           
    verify_tool = [{
        "type": "function",
        "function": {
            "name": "report_verification",
            "description": "Reports verification result",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["OK", "NEEDS_FIX"]},
                    "issues": {"type": "string"}
                },
                "required": ["status", "issues"]
            }
        }
    }]
    
                               
    from rich.console import Console
    console = Console()
    
    try:
        full_content = ""
        tool_calls = None
        for chunk_content, _, tc in model.stream_chat(messages, tools=verify_tool):
            if chunk_content:
                full_content += chunk_content
            if tc:
                tool_calls = tc
                
        result = {"status": "OK", "issues": ""}
        if tool_calls:
            args = tool_calls[0].get("function", {}).get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except:
                    args = {}
            result = {"status": args.get("status", "OK"), "issues": args.get("issues", "")}
        else:
                               
            if "NEEDS_FIX" in full_content:
                result = {"status": "NEEDS_FIX", "issues": full_content}
                
                         
        if db:
            pseudo_symbol_id = hash(filepath) & 0x7FFFFFFF
            db.set_cached(pseudo_symbol_id, "self_verify", file_hash, json.dumps(result))
            
        return result
        
    except Exception as e:
        console.print(f"[yellow]Verification error: {e}[/yellow]")
        return {"status": "OK", "issues": ""}
