import os
import sys

                         
current_dir = os.path.dirname(os.path.abspath(__file__))
vendor_dir = os.path.join(current_dir, "vendor")
sys.path.insert(0, vendor_dir)

import json_schema_to_grammar

                                                          
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..")))
from tools import TOOLS_DEFINITIONS

def main():
                                                          
    tool_schemas = []
    
    for tool in TOOLS_DEFINITIONS:
        if tool.get("type") == "function":
            func = tool["function"]
            
            tool_schema = {
                "type": "object",
                "properties": {
                    "name": {"const": func["name"]},
                    "arguments": func.get("parameters", {"type": "object"})
                },
                "required": ["name", "arguments"],
                "additionalProperties": False
            }
            tool_schemas.append(tool_schema)
            
                 
    root_schema = {
        "anyOf": tool_schemas
    }
    
    converter = json_schema_to_grammar.SchemaConverter(
        prop_order={"name": 1, "arguments": 2},
        allow_fetch=False,
        dotall=False,
        raw_pattern=False
    )
    
    converter.visit(root_schema, "")
    grammar_str = converter.format_grammar()
    
    output_path = os.path.join(current_dir, "tool_call.gbnf")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(grammar_str)
        
    print(f"Wygenerowano pomyślnie gramatykę do: {output_path}")

if __name__ == "__main__":
    main()
