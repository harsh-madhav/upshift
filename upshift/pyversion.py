"""Python version upgrades: which version a repo targets, and the config files that pin it."""

import re
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from .workspace import SKIP_DIRS


def detect(repo: str | Path) -> str | None:
    """The minor version in the repo's .python-version (e.g. "3.10"), if it has one."""
    path = Path(repo) / ".python-version"
    if path.is_file():
        match = re.match(r"\s*(\d+\.\d+)", path.read_text())
        if match:
            return match.group(1)
    return None


def retarget(repo: Path, old: str, new: str) -> list[str]:
    """Move the repo's Python version pins from `old` to `new`. Returns the files changed.

    Covers .python-version, Dockerfile base images, GitHub Actions python-version, tox envs, and in
    pyproject.toml requires-python (only when it excludes the new version) and ruff/black/mypy targets.
    """
    version = re.escape(old) + r"(?:\.\d+)?(?![\d])"
    short_old, short_new = "py" + old.replace(".", ""), "py" + new.replace(".", "")

    def on_lines(text: str, key: str, swap) -> str:
        return "".join(swap(line) if key in line else line for line in text.splitlines(keepends=True))

    def requires_python(text: str) -> str:
        def fix(m: re.Match) -> str:
            try:
                allowed = Version(new) in SpecifierSet(m.group(2))
            except InvalidSpecifier:
                return m.group(0)
            return m.group(0) if allowed else f'{m.group(1)}">={old}"'
        text = re.sub(r'(requires-python\s*=\s*)"([^"]*)"', fix, text)
        text = on_lines(text, "target-version", lambda l: l.replace(short_old, short_new))
        return on_lines(text, "python_version", lambda l: re.sub(rf"(?<![\d.]){version}", new, l))

    rules = {
        ".python-version": lambda t: re.sub(rf"^{version}", new, t, flags=re.M),
        "Dockerfile": lambda t: re.sub(rf"(FROM\s+(?:\S+/)?python:){version}", rf"\g<1>{new}", t),
        "workflow": lambda t: on_lines(t, "python-version", lambda l: re.sub(rf"(?<![\d.]){version}", new, l)),
        "tox.ini": lambda t: re.sub(rf"\b{short_old}\b", short_new, t),
        "pyproject.toml": requires_python,
    }
    changed = []
    for path in sorted(Path(repo).rglob("*")):
        rel = path.relative_to(repo)
        if not path.is_file() or any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name == ".python-version":
            rule = rules[".python-version"]
        elif path.name.startswith("Dockerfile"):
            rule = rules["Dockerfile"]
        elif rel.parts[:2] == (".github", "workflows") and path.suffix in (".yml", ".yaml"):
            rule = rules["workflow"]
        elif path.name in ("tox.ini", "pyproject.toml"):
            rule = rules[path.name]
        else:
            continue
        text = path.read_text()
        updated = rule(text)
        if updated != text:
            path.write_text(updated)
            changed.append(str(rel))
    return changed
