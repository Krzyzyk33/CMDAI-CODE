import ast
import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from .symbol_db import SymbolDB


def extract_symbols_from_code(filepath: str, code: str) -> List[Dict[str, Any]]:
    """
    Ekstraktuje symbole (klasy, funkcje, metody) z kodu źródłowego.
    Używa wbudowanego AST dla Pythona oraz precyzyjnych wyrażeń regularnych dla innych języków.
    """
    ext = os.path.splitext(filepath)[1].lower()
    symbols: List[Dict[str, Any]] = []

    if ext in (".py", ".pyw"):
        try:
            tree = ast.parse(code, filename=filepath)
            lines = code.splitlines()

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    start_line = node.lineno
                    end_line = getattr(node, "end_lineno", start_line)
                    doc = ast.get_docstring(node) or ""

                    args = []
                    for arg in node.args.args:
                        arg_str = arg.arg
                        if arg.annotation:
                            try:
                                arg_str += f": {ast.unparse(arg.annotation)}"
                            except Exception:
                                pass
                        args.append(arg_str)
                    
                    ret = ""
                    if getattr(node, "returns", None):
                        try:
                            ret = f" -> {ast.unparse(node.returns)}"
                        except Exception:
                            pass

                    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
                    signature = f"{prefix}{name}({', '.join(args)}){ret}"

                    chunk_lines = lines[start_line - 1 : min(end_line, start_line + 15)]
                    content = "\n".join(chunk_lines)

                    symbols.append({
                        "name": name,
                        "type": "function",
                        "start_line": start_line,
                        "end_line": end_line,
                        "signature": signature,
                        "docstring": doc,
                        "content": content,
                    })

                elif isinstance(node, ast.ClassDef):
                    name = node.name
                    start_line = node.lineno
                    end_line = getattr(node, "end_lineno", start_line)
                    doc = ast.get_docstring(node) or ""

                    bases = []
                    for b in node.bases:
                        try:
                            bases.append(ast.unparse(b))
                        except Exception:
                            pass
                    bases_str = f"({', '.join(bases)})" if bases else ""
                    signature = f"class {name}{bases_str}"

                    chunk_lines = lines[start_line - 1 : min(end_line, start_line + 15)]
                    content = "\n".join(chunk_lines)

                    symbols.append({
                        "name": name,
                        "type": "class",
                        "start_line": start_line,
                        "end_line": end_line,
                        "signature": signature,
                        "docstring": doc,
                        "content": content,
                    })
        except Exception:
            pass

    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        lines = code.splitlines()
        for i, line in enumerate(lines, 1):
            m_class = re.search(r'\bclass\s+([A-Za-z0-9_$]+)', line)
            if m_class:
                name = m_class.group(1)
                symbols.append({
                    "name": name,
                    "type": "class",
                    "start_line": i,
                    "end_line": min(len(lines), i + 20),
                    "signature": line.strip(),
                    "docstring": "",
                    "content": "\n".join(lines[i-1 : min(len(lines), i + 10)]),
                })
            m_fn = re.search(r'\b(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\((.*?)\)', line)
            if m_fn:
                name = m_fn.group(1)
                symbols.append({
                    "name": name,
                    "type": "function",
                    "start_line": i,
                    "end_line": min(len(lines), i + 15),
                    "signature": line.strip(),
                    "docstring": "",
                    "content": "\n".join(lines[i-1 : min(len(lines), i + 10)]),
                })
            m_arrow = re.search(r'\b(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>', line)
            if m_arrow:
                name = m_arrow.group(1)
                symbols.append({
                    "name": name,
                    "type": "function",
                    "start_line": i,
                    "end_line": min(len(lines), i + 15),
                    "signature": line.strip(),
                    "docstring": "",
                    "content": "\n".join(lines[i-1 : min(len(lines), i + 10)]),
                })

    return symbols


class ProjectIndexer:
    """
    Skaner i zarządca indeksu symboli dla projektu.
    Zapisuje bazę w `.cmdai_code_project/index.db`.
    """

    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self.db_dir = os.path.join(self.workdir, ".cmdai_code_project")
        self.db_path = os.path.join(self.db_dir, "index.db")
        self.db = SymbolDB(self.db_path)

    def index_all(self, force: bool = False) -> Dict[str, Any]:
        """Skanuje i indeksuje pliki w projekcie."""
        supported_exts = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx"}
        ignored_dirs = {
            ".git", "node_modules", "venv", ".venv", "__pycache__",
            ".cmdai_code_project", "dist", "build", ".pytest_cache"
        }

        indexed_files = 0
        total_symbols = 0

        for root, dirs, files in os.walk(self.workdir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]

            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in supported_exts:
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, self.workdir)

                try:
                    mtime = os.path.getmtime(filepath)
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

                    syms = extract_symbols_from_code(rel_path, content)
                    self.db.add_symbols(rel_path, file_hash, mtime, syms)
                    indexed_files += 1
                    total_symbols += len(syms)

                except Exception:
                    continue

        return {
            "indexed_files": indexed_files,
            "total_symbols": total_symbols,
            "db_path": self.db_path,
        }

    def search(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        return self.db.search_symbols(query, limit=limit)
