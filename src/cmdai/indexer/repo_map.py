"""Deterministic, token-budgeted AST Repo Map builder (Aider-style)."""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Set, Tuple

from .ast_indexer import extract_symbols_from_code

SKIP_DIRS: Set[str] = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
    "env", "dist", "build", "target", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".cmdai", "models", "logs", "cache",
}
SUPPORTED_EXTS: Set[str] = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx"}


def build_repo_map(
    workdir: str,
    max_tokens: int = 900,
    max_files: int = 120,
) -> str:
    """Build a deterministic AST symbol outline of `workdir` within `max_tokens`."""
    abs_root = os.path.abspath(workdir or ".")
    if not os.path.isdir(abs_root):
        return ""

    source_files: List[str] = []
    for root, dirs, files in os.walk(abs_root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXTS:
                source_files.append(os.path.join(root, fname))
                if len(source_files) >= max_files:
                    break
        if len(source_files) >= max_files:
            break

    char_budget = max(400, int(max_tokens) * 4)
    chunks: List[str] = []
    used_chars = 0

    for full_path in source_files:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except Exception:
            continue
        if not code.strip():
            continue

        symbols = extract_symbols_from_code(full_path, code)
        if not symbols:
            continue

        # Sort deterministically by line number so prefix stays byte-identical across turns
        symbols.sort(key=lambda s: (int(s.get("start_line", 0)), str(s.get("name", ""))))
        rel_path = os.path.relpath(full_path, abs_root).replace("\\", "/")

        lines = [rel_path]
        seen_sigs: Set[str] = set()
        for sym in symbols[:24]:
            sig = str(sym.get("signature", "") or sym.get("name", "")).strip()
            if not sig or sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            indent = "  " if sym.get("type") == "class" else "    "
            lines.append(f"{indent}{sig}")

        if len(lines) <= 1:
            continue

        block = "\n".join(lines)
        if used_chars + len(block) + 2 > char_budget:
            if not chunks:
                chunks.append(block[:char_budget])
            break
        chunks.append(block)
        used_chars += len(block) + 2

    dep_summary = build_graph_context_summary(abs_root, max_edges=16)
    if dep_summary and used_chars + len(dep_summary) + 2 <= char_budget:
        chunks.append(dep_summary)

    return "\n\n".join(chunks)


def build_repo_graph(workdir: str, max_files: int = 45) -> Dict[str, Any]:
    """Build a node-and-edge graph of workspace directories, files, symbols, and imports."""
    abs_root = os.path.abspath(workdir or ".")
    root_name = os.path.basename(abs_root) or abs_root
    nodes: List[Dict[str, Any]] = [
        {"id": "root", "label": root_name, "kind": "root", "path": ".", "symbols": []}
    ]
    edges: List[Tuple[str, str, str]] = []  # (src_id, dst_id, kind)
    node_ids: Set[str] = {"root"}

    if not os.path.isdir(abs_root):
        return {"nodes": nodes, "edges": edges}

    source_files: List[str] = []
    for root, dirs, files in os.walk(abs_root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fname in sorted(files):
            if os.path.splitext(fname)[1].lower() in SUPPORTED_EXTS:
                source_files.append(os.path.join(root, fname))
                if len(source_files) >= max_files:
                    break
        if len(source_files) >= max_files:
            break

    module_to_node: Dict[str, str] = {}
    file_imports: List[Tuple[str, List[str]]] = []

    for full_path in source_files:
        rel_path = os.path.relpath(full_path, abs_root).replace("\\", "/")
        parts = rel_path.split("/")
        parent_id = "root"
        if len(parts) > 1:
            top_dir = parts[0]
            dir_id = f"dir:{top_dir}"
            if dir_id not in node_ids:
                node_ids.add(dir_id)
                nodes.append({"id": dir_id, "label": top_dir + "/", "kind": "dir", "path": top_dir, "symbols": []})
                edges.append(("root", dir_id, "contains"))
            parent_id = dir_id

        file_id = f"file:{rel_path}"
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except Exception:
            code = ""

        syms = extract_symbols_from_code(full_path, code) if code.strip() else []
        sym_labels = [
            f"{s.get('type', 'sym')}: {s.get('name', '')}"
            for s in syms[:16]
            if s.get("name")
        ]
        if file_id not in node_ids:
            node_ids.add(file_id)
            nodes.append({
                "id": file_id,
                "label": os.path.basename(rel_path),
                "kind": "file",
                "path": rel_path,
                "symbols": sym_labels,
            })
            edges.append((parent_id, file_id, "contains"))

        stem = os.path.splitext(rel_path)[0].replace("/", ".")
        module_to_node[stem] = file_id
        module_to_node[os.path.splitext(os.path.basename(rel_path))[0]] = file_id

        if full_path.endswith((".py", ".pyw")) and code.strip():
            imps: List[str] = []
            pkg_parts = rel_path.split("/")[:-1]
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imps.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        base = node.module or ""
                        if node.level > 0:
                            up = max(0, len(pkg_parts) - (node.level - 1))
                            prefix = ".".join(pkg_parts[:up])
                            base = f"{prefix}.{base}".strip(".") if base else prefix
                        if base:
                            imps.append(base)
                        for alias in node.names:
                            if alias.name != "*":
                                imps.append(f"{base}.{alias.name}".strip(".") if base else alias.name)
            except Exception:
                pass
            if imps:
                file_imports.append((file_id, imps))

    seen_imp_edges: Set[Tuple[str, str]] = set()
    for src_id, imps in file_imports:
        for mod in imps:
            tail = mod.split(".")[-1]
            dst_id = module_to_node.get(mod) or module_to_node.get(tail)
            if dst_id and dst_id != src_id and (src_id, dst_id) not in seen_imp_edges:
                seen_imp_edges.add((src_id, dst_id))
                edges.append((src_id, dst_id, "import"))

    return {"nodes": nodes, "edges": edges}


def build_graph_context_summary(workdir: str, max_edges: int = 18) -> str:
    """Return a compact, deterministic Graphify-style dependency summary for LLM context."""
    graph = build_repo_graph(workdir)
    imp_edges = [(s.replace("file:", ""), d.replace("file:", "")) for s, d, k in graph.get("edges", []) if k == "import"]
    if not imp_edges:
        return ""
    lines = ["[Dependency Graph]"]
    for src, dst in sorted(imp_edges)[:max_edges]:
        lines.append(f"  {src} -> {dst}")
    return "\n".join(lines)
