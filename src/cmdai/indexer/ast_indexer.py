import ast
import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from .symbol_db import SymbolDB


def extract_symbols_from_code(filepath: str, code: str) -> List[Dict[str, Any]]:
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

    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self.db_dir = os.path.join(self.workdir, ".cmdai_code_project")
        self.db_path = os.path.join(self.db_dir, "index.db")
        self.db = SymbolDB(self.db_path)

    def index_file(self, filepath: str) -> Dict[str, Any]:
        full = filepath if os.path.isabs(filepath) else os.path.join(self.workdir, filepath)
        rel_path = os.path.relpath(full, self.workdir)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"path": rel_path, "skipped": False, "error": str(e)}
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if self.db.get_file_hash(rel_path) == file_hash:
            return {"path": rel_path, "skipped": True, "symbols": 0}
        syms = extract_symbols_from_code(rel_path, content)
        self.db.add_symbols(rel_path, file_hash, os.path.getmtime(full), syms)
        return {"path": rel_path, "skipped": False, "symbols": len(syms)}

    def index_all(self, force: bool = False) -> Dict[str, Any]:
        supported_exts = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx"}
        ignored_dirs = {
            ".git", "node_modules", "venv", ".venv", "__pycache__",
            ".cmdai_code_project", "dist", "build", ".pytest_cache"
        }

        indexed_files = 0
        skipped_files = 0
        removed_files = 0
        total_symbols = 0
        seen: set = set()

        for root, dirs, files in os.walk(self.workdir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]

            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in supported_exts:
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, self.workdir)
                seen.add(rel_path)

                try:
                    mtime = os.path.getmtime(filepath)
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if not force and self.db.get_file_hash(rel_path) == file_hash:
                        skipped_files += 1
                        continue

                    syms = extract_symbols_from_code(rel_path, content)
                    self.db.add_symbols(rel_path, file_hash, mtime, syms)
                    indexed_files += 1
                    total_symbols += len(syms)

                except Exception:
                    continue

        for stale in self._db_filepaths() - seen:
            try:
                self.db.clear_file_symbols(stale)
                removed_files += 1
            except Exception:
                pass

        stats = self.db.get_stats()
        return {
            "indexed_files": indexed_files,
            "skipped_files": skipped_files,
            "removed_files": removed_files,
            "total_symbols": total_symbols,
            "db_files": stats["files"],
            "db_symbols": stats["symbols"],
            "db_path": self.db_path,
        }

    def _db_filepaths(self) -> set:
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("SELECT filepath FROM files").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def search(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        return self.db.search_symbols(query, limit=limit)
