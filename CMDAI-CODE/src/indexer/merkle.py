import os
import hashlib
from typing import Dict, Any, List

def hash_file(filepath: str) -> str:
    """Calculates the SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""

def build_tree(directory: str, ignore_dirs: List[str] = None) -> Dict[str, str]:
    """
    Builds a flat 'tree' representing relative file paths to their hashes.
    Uses relative paths to be portable.
    """
    if ignore_dirs is None:
        ignore_dirs = [".git", "__pycache__", "node_modules", ".pytest_cache", "venv", "env", ".cmdai_code_project"]
        
    tree = {}
    for root, dirs, files in os.walk(directory):
                                              
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file in files:
                                                               
            if file.startswith('.'):
                continue
                
            abs_path = os.path.join(root, file)
            try:
                rel_path = os.path.relpath(abs_path, directory)
                                       
                rel_path = rel_path.replace("\\", "/")
                file_hash = hash_file(abs_path)
                if file_hash:
                    tree[rel_path] = file_hash
            except Exception:
                continue
    return tree

def diff_trees(old_tree: Dict[str, str], new_tree: Dict[str, str]) -> Dict[str, List[str]]:
    """
    Compares two state trees and returns files that were added, modified, or deleted.
    """
    added = []
    modified = []
    deleted = []
    
    for path, new_hash in new_tree.items():
        if path not in old_tree:
            added.append(path)
        elif old_tree[path] != new_hash:
            modified.append(path)
            
    for path in old_tree.keys():
        if path not in new_tree:
            deleted.append(path)
            
    return {
        "added": added,
        "modified": modified,
        "deleted": deleted
    }
