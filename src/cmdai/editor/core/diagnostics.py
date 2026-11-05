from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_GRAMMAR_MODULES: Dict[str, str] = {
    ".py": "tree_sitter_python",
    ".pyi": "tree_sitter_python",
    ".js": "tree_sitter_javascript",
    ".mjs": "tree_sitter_javascript",
    ".cjs": "tree_sitter_javascript",
    ".jsx": "tree_sitter_javascript",
    ".go": "tree_sitter_go",
    ".rs": "tree_sitter_rust",
    ".html": "tree_sitter_html",
    ".htm": "tree_sitter_html",
    ".css": "tree_sitter_css",
    ".json": "tree_sitter_json",
    ".jsonc": "tree_sitter_json",
    ".yaml": "tree_sitter_yaml",
    ".yml": "tree_sitter_yaml",
    ".toml": "tree_sitter_toml",

    ".sh": "tree_sitter_bash",
    ".bash": "tree_sitter_bash",

}

NO_GRAMMAR = {".bat", ".cmd", ".ps1", ".ts", ".tsx", ".c", ".h", ".cpp",
              ".hpp", ".cs", ".java", ".kt", ".rb", ".php", ".swift",
              ".lua", ".vue", ".svelte", ".sql", ".xml", ".ini", ".cfg",
              ".env", ".gitignore", ".md", ".markdown"}

_LANG_CACHE: Dict[str, Any] = {}

@dataclass
class Diagnostic:

    path: str
    row: int
    col: int
    severity: str
    code: str
    message: str
    source: str
    end_col: int = 0

    @property
    def key(self) -> Tuple[str, int, int]:
        return (self.path, self.row, self.col)

_RUFF_ERROR_CODES = {"F821", "F811", "F632", "F701", "F702", "F706"}
_RUFF_SYNTAX_CODES = {"invalid-syntax"}

def _severity_for_ruff(code: str) -> str:
    if code in _RUFF_SYNTAX_CODES or code.startswith("E9"):
        return "error"
    if code in _RUFF_ERROR_CODES:
        return "error"
    return "warning"

def get_language(ext: str):

    ext = ext.lower()
    if ext in _LANG_CACHE:
        return _LANG_CACHE[ext]
    module_name = _GRAMMAR_MODULES.get(ext)
    lang = None
    if module_name:
        try:
            from tree_sitter import Language
            mod = importlib.import_module(module_name)
            lang = Language(mod.language())
        except Exception:
            lang = None
    _LANG_CACHE[ext] = lang
    return lang

def supported_extensions() -> List[str]:
    return sorted(_GRAMMAR_MODULES)

def has_grammar(ext: str) -> bool:
    return get_language(ext) is not None

class TreeSitterLinter:

    def __init__(self) -> None:
        self._parsers: Dict[str, Any] = {}

    def _parser_for(self, ext: str):
        if ext in self._parsers:
            return self._parsers[ext]
        parser = None
        lang = get_language(ext)
        if lang is not None:
            try:
                from tree_sitter import Parser
                parser = Parser(lang)
            except Exception:
                parser = None
        self._parsers[ext] = parser
        return parser

    def check(self, path: str, text: str) -> List[Diagnostic]:
        ext = os.path.splitext(path)[1]
        parser = self._parser_for(ext)
        if parser is None:
            return []
        try:
            data = text.encode("utf-8", "replace")
            tree = parser.parse(data)
        except Exception:
            return []
        out: List[Diagnostic] = []
        self._walk(tree.root_node, data, path, out)
        return out

    def _walk(self, node, data: bytes, path: str, out: List[Diagnostic]) -> None:

        if node.is_missing:
            out.append(Diagnostic(
                path=path,
                row=node.start_point[0],
                col=node.start_point[1],
                end_col=node.end_point[1],
                severity="error",
                code="missing",
                message=f"Missing {node.type}",
                source="tree-sitter",
            ))
            return
        if node.type == "ERROR":
            snippet = data[node.start_byte:node.end_byte][:60]
            text_snippet = snippet.decode("utf-8", "replace")
            text_snippet = " ".join(text_snippet.split())
            out.append(Diagnostic(
                path=path,
                row=node.start_point[0],
                col=node.start_point[1],
                end_col=node.end_point[1],
                severity="error",
                code="syntax",
                message=f"Syntax error: {text_snippet}" if text_snippet
                        else "Syntax error",
                source="tree-sitter",
            ))
            return
        for child in node.children:
            self._walk(child, data, path, out)

class RuffLinter:

    SELECT = "E9,F"

    def __init__(self) -> None:
        self._exe: Optional[str] = shutil.which("ruff")
        if self._exe is None:

            try:
                res = subprocess.run(
                    [sys.executable, "-m", "ruff", "--version"],
                    capture_output=True, timeout=15)

                self._via_module = res.returncode == 0
            except Exception:
                self._via_module = False
        else:
            self._via_module = False

    @property
    def available(self) -> bool:
        return bool(self._exe) or self._via_module

    def _argv(self, path: str) -> List[str]:
        base = ([sys.executable, "-m", "ruff"] if self._via_module
                else [self._exe or "ruff"])
        return base + [
            "check", "--output-format", "json", "--no-cache",
            "--select", self.SELECT,
            "--stdin-filename", path, "-",
        ]

    def check(self, path: str, text: str) -> List[Diagnostic]:
        if not self.available:
            return []
        if os.path.splitext(path)[1].lower() not in (".py", ".pyi"):
            return []
        try:
            res = subprocess.run(
                self._argv(path), input=text, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=30)
        except Exception:
            return []
        out = res.stdout.strip()
        if not out:
            return []
        try:
            import json
            data = json.loads(out)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        diags: List[Diagnostic] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            loc = item.get("location") or {}
            end = item.get("end_location") or {}
            row = int(loc.get("row", 1)) - 1
            col = int(loc.get("column", 1)) - 1
            code = str(item.get("code") or "")
            diags.append(Diagnostic(
                path=path,
                row=max(0, row),
                col=max(0, col),
                end_col=int(end.get("column", col + 1)) - 1,
                severity=_severity_for_ruff(code),
                code=code,
                message=str(item.get("message") or code),
                source="ruff",
            ))
        return diags

    def check_paths(self, paths: List[str]) -> List[Diagnostic]:

        if not self.available:
            return []
        py = [p for p in paths
              if os.path.splitext(p)[1].lower() in (".py", ".pyi")]
        if not py:
            return []
        base = ([sys.executable, "-m", "ruff"] if self._via_module
                else [self._exe or "ruff"])
        argv = base + [
            "check", "--output-format", "json", "--no-cache",
            "--select", self.SELECT, "--quiet", *py,
        ]
        try:
            res = subprocess.run(
                argv, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
        except Exception:
            return []
        out = res.stdout.strip()
        if not out:
            return []
        try:
            import json
            data = json.loads(out)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        diags: List[Diagnostic] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            path = str(item.get("filename") or "")
            if not path:
                continue
            loc = item.get("location") or {}
            end = item.get("end_location") or {}
            row = int(loc.get("row", 1)) - 1
            col = int(loc.get("column", 1)) - 1
            code = str(item.get("code") or "")
            diags.append(Diagnostic(
                path=os.path.abspath(path),
                row=max(0, row),
                col=max(0, col),
                end_col=int(end.get("column", col + 1)) - 1,
                severity=_severity_for_ruff(code),
                code=code,
                message=str(item.get("message") or code),
                source="ruff",
            ))
        return diags

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", ".tox", ".idea", ".vscode",
    "site-packages", ".gradle", "target", "vendor", ".cache",
    "coverage", "htmlcov", ".terraform", "obj", "bin",
}

MAX_SCAN_FILES = 600

@dataclass
class ProjectScan:

    diagnostics: List[Diagnostic] = field(default_factory=list)
    checked: int = 0
    truncated: bool = False
    note: str = ""

def scan_project(root: str, registry: Optional["LinterRegistry"] = None,
                 limit: int = MAX_SCAN_FILES) -> ProjectScan:

    reg = registry or linter
    scan = ProjectScan()
    if not root or not os.path.isdir(root):
        scan.note = "- folder does not exist"
        return scan

    exts = set(_GRAMMAR_MODULES)
    py_files: List[str] = []
    texts: List[tuple] = []

    for dirpath, dirnames, filenames in os.walk(root):

        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS
                             and not d.startswith(".")
                             and d.lower() not in SKIP_DIRS)
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                continue
            if scan.checked >= limit:
                scan.truncated = True
                break
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > 1_500_000:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            scan.checked += 1
            if ext in (".py", ".pyi"):
                py_files.append(full)
            else:
                texts.append((full, text))
        if scan.truncated:
            break

    for full, text in texts:
        scan.diagnostics.extend(reg.tree_sitter.check(full, text))
    if py_files:
        scan.diagnostics.extend(reg.ruff.check_paths(py_files))

    scan.diagnostics = dedupe(scan.diagnostics)
    scan.diagnostics.sort(key=lambda d: (d.path.lower(), d.row, d.col))
    if scan.truncated:
        scan.note = f"- skan ograniczony do {limit} plikow"
    return scan

def dedupe(diags: List[Diagnostic]) -> List[Diagnostic]:

    seen = set()
    out: List[Diagnostic] = []
    for d in sorted(diags, key=lambda x: (x.row, x.col, x.source)):
        key = (d.path, d.row)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out

class LinterRegistry:

    def __init__(self) -> None:
        self.tree_sitter = TreeSitterLinter()
        self.ruff = RuffLinter()
        self._last_note: str = ""

    def engine_note(self) -> str:

        n = len(supported_extensions())
        if self.ruff.available:
            return f"(tree-sitter: {n} jezykow + ruff)"
        return f"(tylko skladnia, {n} jezykow - zainstaluj: pip install ruff)"

    def check(self, path: str, text: str) -> List[Diagnostic]:

        if not path:
            return []
        return dedupe(self.tree_sitter.check(path, text))

    def check_full(self, path: str, text: str) -> List[Diagnostic]:

        if not path:
            return []
        diags = self.tree_sitter.check(path, text)
        diags += self.ruff.check(path, text)
        return dedupe(diags)

    def note_for(self, path: str) -> str:

        ext = os.path.splitext(path)[1].lower()
        if ext in _GRAMMAR_MODULES:
            if has_grammar(ext):
                note = ""
                if not self.ruff.available and ext in (".py", ".pyi"):
                    note = "- brak ruff: tylko bledy skladni"
                return note
            return f"- gramatyka tree-sitter dla {ext} nie wczytala sie"
        if ext in (".md", ".markdown"):
            return f"- {ext}: gramatka nie raportuje bledow skladni"
        if ext in NO_GRAMMAR:
            return f"- brak gramatyki dla {ext}"
        return f"- {ext or 'bez rozszerzenia'}: brak obslugi"

linter = LinterRegistry()
