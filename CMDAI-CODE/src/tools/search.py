import os
import re
import glob as pyglob
import subprocess
import shutil
from typing import List, Dict, Any, Optional

def run_grep(pattern: str, path: str = ".", glob_pattern: Optional[str] = None) -> str:
                                            
    results = []
    if os.path.isfile(path):
        files = [path]
    else:
        files = []
        for root, _, filenames in os.walk(path):
            for f in filenames:
                if glob_pattern:
                    import fnmatch
                    if not fnmatch.fnmatch(f, glob_pattern):
                        continue
                files.append(os.path.join(root, f))
                
    try:
        regex = re.compile(pattern)
        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if regex.search(line):
                            results.append(f"{filepath}:{i+1}:{line.strip()}")
            except (UnicodeDecodeError, PermissionError):
                continue
    except Exception as e:
        return f"Error: {str(e)}"
        
    if not results:
        return "No matches found."
    return "\n".join(results[:500])

def search_web(query: str) -> str:
    if query.startswith("http://") or query.startswith("https://"):
        try:
            import urllib.request
            import re
            req = urllib.request.Request(query, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
                                                            
                html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                html = re.sub(r'<style.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:4000]
        except Exception as e:
            return f"Error reading URL: {e}"

    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            
        if not results:
            return f"No results found for \"{query}\"."
            
        out = f"Results for \"{query}\":\n\n"
        for i, res in enumerate(results):
            title = res.get('title', 'No title')
            href = res.get('href', '')
            body = res.get('body', 'No snippet available')
            out += f"{i+1}. {title}\n   Link: {href}\n   Snippet: {body}\n\n"
        return out
    except Exception as e:
        return f"Error executing web search: {str(e)}"

def code_search(query: str, path: str = ".") -> str:
    """
    Przeszukuje kod projektu przy pomocy bazy SQLite FTS5.
    W razie braku bazy spada do rg lub grep_search.
    """
    db_path = os.path.join(path, ".cmdai_code_project", "index.db")
    if os.path.exists(db_path):
        try:
                                            
            sys_path_added = False
            if "src" not in sys.path:
                import sys
                sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
                sys_path_added = True
                
            from indexer.symbol_db import SymbolDB
            db = SymbolDB(db_path)
            results = db.search_symbols(query)
            
            if sys_path_added:
                sys.path.pop(0)
                
            if results:
                out = [f"FTS5 Results for '{query}':"]
                for r in results:
                    out.append(f"{r['filepath']}:{r['start_line']} [{r['symbol_type']}] {r['name']}")
                                          
                    lines = r['content'].split('\n')
                    snippet = "\n".join(lines[:3]) + ("..." if len(lines) > 3 else "")
                    out.append(f"Snippet:\n{snippet}\n")
                return "\n".join(out)
        except Exception as e:
            pass           
            
                                      
    if shutil.which("rg"):
        try:
            result = subprocess.run(["rg", "-n", query, path], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()[:4000]
        except Exception:
            pass
            
    return run_grep(query, path)
