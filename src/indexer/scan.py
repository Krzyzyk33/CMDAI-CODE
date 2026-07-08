import os
import json
from .merkle import build_tree, diff_trees
from .ts_parser import parse_file
from .symbol_db import SymbolDB

def get_project_dir(cwd: str) -> str:
    proj_dir = os.path.join(cwd, ".cmdai_code_project")
    os.makedirs(proj_dir, exist_ok=True)
    return proj_dir

def get_language_from_ext(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".py":
        return "python"
    elif ext in [".js", ".jsx", ".mjs"]:
        return "javascript"
    elif ext in [".ts", ".tsx"]:
        return "typescript"
    return ""

def full_or_incremental_scan(cwd: str):
    """
    Uruchamiane podczas startu sesji w main.py.
    Skanuje zmienione pliki i aktualizuje bazę indeksu oraz drzewo Merkle.
    """
    proj_dir = get_project_dir(cwd)
    db_path = os.path.join(proj_dir, "index.db")
    merkle_state_path = os.path.join(proj_dir, "merkle_state.json")
    
                            
    old_tree = {}
    if os.path.exists(merkle_state_path):
        try:
            with open(merkle_state_path, "r", encoding="utf-8") as f:
                old_tree = json.load(f)
        except Exception:
            pass
            
                        
    new_tree = build_tree(cwd)
    
             
    diff = diff_trees(old_tree, new_tree)
    
    added_or_modified = diff["added"] + diff["modified"]
    deleted = diff["deleted"]
    
    if not added_or_modified and not deleted:
                                        
        return
        
    db = SymbolDB(db_path)
    
              
    for rel_path in deleted:
        db.delete_file(os.path.abspath(os.path.join(cwd, rel_path)))
        
                              
    for rel_path in added_or_modified:
        abs_path = os.path.abspath(os.path.join(cwd, rel_path))
        file_hash = new_tree[rel_path]
        language = get_language_from_ext(abs_path)
        
                                                  
        if not language:
            continue
            
        symbols = parse_file(abs_path, language)
        db.upsert_file_symbols(abs_path, file_hash, symbols)
        
                      
    with open(merkle_state_path, "w", encoding="utf-8") as f:
        json.dump(new_tree, f, indent=2)
