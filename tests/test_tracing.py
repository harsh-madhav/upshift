import ast
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "upshift"


def test_spans_never_capture_the_model_client():
    """neatlogs records a span's arguments; the llm object holds the provider's API key."""
    leaks = []
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if "llm" not in [a.arg for a in node.args.args + node.args.kwonlyargs]:
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "neatlogs.span":
                    captured = {k.arg: ast.unparse(k.value) for k in dec.keywords}.get("capture_input") != "False"
                    if captured:
                        leaks.append(f"{path.name}:{node.lineno} {node.name}")
    assert not leaks, f"spans that would upload the API key: {leaks}"
