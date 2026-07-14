import os
from typing import List, Dict, Any

class Symbol:
    def __init__(self, name: str, symbol_type: str, filepath: str, start_line: int, end_line: int, content: str):
        self.name = name
        self.symbol_type = symbol_type
        self.filepath = filepath
        self.start_line = start_line
        self.end_line = end_line
        self.content = content

def get_parser(language: str):
    """
    Inicjalizuje parser Tree-sitter dla danego języka.
    Wymaga zainstalowanych bibliotek np. tree-sitter, tree-sitter-python.
    """
    try:
        from tree_sitter import Language, Parser
        
        parser = Parser()
        if language == "python":
            import tree_sitter_python as tspython
            lang = Language(tspython.language(), "python")
        elif language in ["javascript", "js", "ts", "typescript"]:
            import tree_sitter_javascript as tsjs
            lang = Language(tsjs.language(), "javascript")
        else:
            return None
            
        parser.set_language(lang)
        return parser
    except ImportError:
        return None

def parse_file(filepath: str, language: str) -> List[Symbol]:
    """Parses a file and extracts symbols (functions, classes)."""
    parser = get_parser(language)
    if not parser:
                                                                               
        return parse_file_fallback(filepath, language)
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            code_str = f.read()
    except Exception:
        return []
        
    tree = parser.parse(bytes(code_str, "utf8"))
    
    symbols = []
    
                           
                                                                    
    try:
        from tree_sitter import Language
        query_str = ""
        if language == "python":
            query_str = """
            (function_definition
              name: (identifier) @func.name) @func.def
            (class_definition
              name: (identifier) @class.name) @class.def
            """
        elif language in ["javascript", "js", "typescript", "ts"]:
            query_str = """
            (function_declaration
              name: (identifier) @func.name) @func.def
            (class_declaration
              name: (identifier) @class.name) @class.def
            """
            
        if query_str:
            query = parser.language.query(query_str)
            captures = query.captures(tree.root_node)
            
                                                                                         
            node_map = {}
            for node, capture_name in captures:
                if capture_name.endswith(".name"):
                    base = capture_name.split(".")[0]
                    if base not in node_map:
                        node_map[base] = []
                    node_map[base].append({"name_node": node, "def_node": None})
                elif capture_name.endswith(".def"):
                    base = capture_name.split(".")[0]
                                                 
                    if base in node_map and len(node_map[base]) > 0:
                        node_map[base][-1]["def_node"] = node
                        
            for sym_type, nodes in node_map.items():
                for pair in nodes:
                    if pair["name_node"] and pair["def_node"]:
                        n_node = pair["name_node"]
                        d_node = pair["def_node"]
                        
                        sym_name = code_str[n_node.start_byte:n_node.end_byte]
                        sym_content = code_str[d_node.start_byte:d_node.end_byte]
                        
                        symbol = Symbol(
                            name=sym_name,
                            symbol_type=sym_type,
                            filepath=filepath,
                            start_line=d_node.start_point[0] + 1,
                            end_line=d_node.end_point[0] + 1,
                            content=sym_content
                        )
                        symbols.append(symbol)
                        
    except Exception as e:
        print(f"Błąd analizy tree-sitter: {e}")
        
    return symbols

def parse_file_fallback(filepath: str, language: str) -> List[Symbol]:
    """Fallback jeśli tree-sitter nie zadziała."""
    import re
    symbols = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        if language == "python":
            func_pattern = re.compile(r"^def\s+([a-zA-Z0-9_]+)\s*\(")
            class_pattern = re.compile(r"^class\s+([a-zA-Z0-9_]+)\s*[:\(]")
            
            for i, line in enumerate(lines):
                m_func = func_pattern.match(line)
                if m_func:
                    name = m_func.group(1)
                    symbols.append(Symbol(name, "func", filepath, i+1, i+1, line.strip()))
                m_class = class_pattern.match(line)
                if m_class:
                    name = m_class.group(1)
                    symbols.append(Symbol(name, "class", filepath, i+1, i+1, line.strip()))
    except Exception:
        pass
    return symbols
