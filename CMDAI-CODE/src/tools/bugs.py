import os
import ast

def run_bugs() -> str:
    """
    Skanuje cały projekt (.) pod kątem błędów składniowych.
    Zwraca natywne logi błędów z kompilatora Pythona.
    """
    path = "."
    files_to_check = []
    for root, _, files in os.walk(path):
        if ".cmdai_code_project" in root or "__pycache__" in root or "venv" in root or "node_modules" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                files_to_check.append(os.path.join(root, f))

    if not files_to_check:
        return "No python files found to check."

    errors_by_file = {}
    for fpath in files_to_check:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                source = f.read()
            ast.parse(source, filename=fpath)
        except SyntaxError as e:
            if fpath not in errors_by_file:
                errors_by_file[fpath] = []
            errors_by_file[fpath].append(f"|_ Line {e.lineno} - SyntaxError: {e.msg}")
        except Exception as e:
            if fpath not in errors_by_file:
                errors_by_file[fpath] = []
            errors_by_file[fpath].append(f"|_ Check failed - {str(e)}")

    if not errors_by_file:
        return "No syntax errors found."

    output = []
    for fpath, errs in errors_by_file.items():
        output.append(fpath)
        for err in errs:
            output.append(f"   {err}")

    return "\n".join(output)
