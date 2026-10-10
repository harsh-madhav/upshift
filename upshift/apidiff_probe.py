"""Run inside a throwaway venv: print the public API of one installed distribution as JSON.

Usage: python apidiff_probe.py <distribution-name>
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


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level_modules(dist: str) -> list[str]:
    wanted = norm(dist)
    return sorted(
        name for name, dists in md.packages_distributions().items()
        if not name.startswith("_") and any(norm(d) == wanted for d in dists)
    )


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
    for top in top_level_modules(sys.argv[1]):
        try:
            root = importlib.import_module(top)
        except Exception:
            continue
        modules = [root]
        if hasattr(root, "__path__"):
            for info in pkgutil.walk_packages(root.__path__, top + ".", onerror=lambda _: None):
                if any(part.startswith("_") for part in info.name.split(".")[1:]):
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
                if len(api) >= MAX_SYMBOLS:
                    break
    json.dump(api, sys.stdout)


if __name__ == "__main__":
    main()
