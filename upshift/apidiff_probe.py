"""Run inside a throwaway venv: print the public API of one installed distribution as JSON.

Usage: python apidiff_probe.py <distribution-name>
       python apidiff_probe.py --stdlib      (the interpreter's standard library instead)
Output: {"module.attr": "signature or kind", ...}
"""

import importlib
import importlib.metadata as md
import inspect
import json
import pkgutil
import re
import sys
import warnings

MAX_SYMBOLS = 20000
# stdlib modules that are huge, GUI-only, or do something on import (antigravity opens a browser)
STDLIB_SKIP = {"antigravity", "this", "idlelib", "tkinter", "turtle", "turtledemo", "test", "lib2to3",
               "ensurepip", "pydoc_data", "venv", "__phello__"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level_modules(dist: str) -> list[str]:
    wanted = norm(dist)
    return sorted(
        name for name, dists in md.packages_distributions().items()
        if not name.startswith("_") and any(norm(d) == wanted for d in dists)
    )


def stdlib_modules() -> list[str]:
    return sorted(n for n in sys.stdlib_module_names if not n.startswith("_") and n not in STDLIB_SKIP)


def describe(obj) -> str:
    try:
        if inspect.isclass(obj):
            return "class" + str(inspect.signature(obj))
        if callable(obj):
            return "def" + str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "class" if inspect.isclass(obj) else "callable"
    return type(obj).__name__


def main() -> None:
    warnings.simplefilter("ignore")
    api: dict[str, str] = {}
    stdlib = sys.argv[1] == "--stdlib"
    limit = MAX_SYMBOLS * 5 if stdlib else MAX_SYMBOLS
    for top in stdlib_modules() if stdlib else top_level_modules(sys.argv[1]):
        try:
            root = importlib.import_module(top)
        except Exception:
            continue
        modules = [root]
        if hasattr(root, "__path__"):
            for info in pkgutil.walk_packages(root.__path__, top + ".", onerror=lambda _: None):
                if any(part.startswith("_") or part in STDLIB_SKIP for part in info.name.split(".")[1:]):
                    continue
                try:
                    modules.append(importlib.import_module(info.name))
                except Exception:
                    pass
        for module in modules:
            # __all__ covers packages that expose names lazily through module __getattr__ (pydantic v2 does)
            names = getattr(module, "__all__", None) or list(vars(module))
            for name in sorted(set(names)):
                try:
                    obj = getattr(module, name)
                except Exception:
                    continue
                if name.startswith("_") or inspect.ismodule(obj):
                    continue
                key = f"{module.__name__}.{name}"
                api[key] = describe(obj)
                if inspect.isclass(obj) and getattr(obj, "__module__", "") == module.__name__:
                    for member, value in sorted(vars(obj).items()):
                        if not member.startswith("_") or member in ("__init__",):
                            api[f"{key}.{member}"] = describe(value) if callable(value) else type(value).__name__
                if len(api) >= limit:
                    break
    json.dump(api, sys.stdout)


if __name__ == "__main__":
    main()
