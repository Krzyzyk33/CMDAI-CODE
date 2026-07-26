import os
import ast

def run_bugs() -> str:
    """
    Skanuje cały projekt (.) pod kątem błędów składniowych.
    Zwraca strukturę drzewa z wykazem plików i wyciągniętymi liniami błędów.
    """
    path = "."
    files_to_check = []
    for root, dirs, files in os.walk(path):
        if ".cmdai_code_project" in root or "__pycache__" in root or "venv" in root or "node_modules" in root or ".git" in root:
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
            msg = e.msg or "Syntax error"
            errors_by_file[fpath].append(f"Line {e.lineno} - SyntaxError: {msg}")
        except Exception as e:
            if fpath not in errors_by_file:
                errors_by_file[fpath] = []
            errors_by_file[fpath].append(f"Check failed - {str(e)}")

    if not errors_by_file:
        return "No syntax errors found."

    output = ["bugs:"]
    for fpath, errs in errors_by_file.items():
        clean_path = os.path.normpath(fpath)
        output.append(f"  └ {clean_path}")
        for err in errs:
            output.append(f"      └ {err}")

    return "\n".join(output)
