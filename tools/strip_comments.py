"""Strip # comments (tokenize, strings safe) and docstrings (line ranges, formatting kept)."""
import ast
import io
import os
import tokenize

ROOTS = ["src", "tools"]
TOP_FILES = ["cmdai.py"]

count_comments = 0
count_docstrings = 0


def strip_comments(src: str) -> str:
    global count_comments
    out = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
    except Exception:
        return src
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            count_comments += 1
            continue
        out.append(tok)
    return tokenize.untokenize(out)


def docstring_spans(tree: ast.AST):
    spans = []
    for node in ast.walk(tree):
        bodies = []
        if isinstance(node, ast.Module):
            bodies = [node.body]
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies = [node.body]
        for body in bodies:
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ds = body[0]
                spans.append((ds.lineno, ds.end_lineno, len(body) == 1,
                              " " * (ds.col_offset if ds.col_offset else 0)))
    return spans


def process(path: str) -> bool:
    with open(path, "r", encoding="utf-8-sig") as f:
        src = f.read()
    src2 = strip_comments(src)
    try:
        tree = ast.parse(src2)
    except SyntaxError as e:
        print(f"SKIP (syntax): {path}: {e}")
        return False
    spans = docstring_spans(tree)
    lines = src2.splitlines(keepends=True)
    global count_docstrings
    for start, end, sole, indent in sorted(spans, reverse=True):
        count_docstrings += 1
        if sole:
            lines[start - 1:end] = [indent + "pass\n"]
        else:
            del lines[start - 1:end]
    new_src = "".join(lines)
    try:
        compile(new_src, path, "exec")
    except SyntaxError as e:
        print(f"SKIP (regen broken): {path}: {e}")
        return False
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_src)
    return True


def main() -> None:
    files = []
    for root in ROOTS:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.endswith(".py") and fn != "strip_comments.py":
                    files.append(os.path.join(dp, fn))
    for tf in TOP_FILES:
        if os.path.exists(tf):
            files.append(tf)
    ok = 0
    for p in sorted(files):
        print(f"... {p}", flush=True)
        if process(p):
            ok += 1
    print(f"files: {ok}/{len(files)}, comments removed: {count_comments}, docstrings removed: {count_docstrings}")


if __name__ == "__main__":
    main()
