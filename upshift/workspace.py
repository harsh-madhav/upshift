"""The target repo's working copy: clone, venv, dependency pins, tests, git checkpoints."""

import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import neatlogs
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache"}
OUTPUT_CAP = 12000
PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==\S+$")


def sh(cmd: list[str], cwd: str | Path | None = None, timeout: int = 900, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


def tail(text: str, limit: int = OUTPUT_CAP) -> str:
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]


@dataclass
class TestRun:
    exit_code: int
    failing: set[str]
    output: str
    collection_error: bool

    @property
    def passed(self) -> int:
        m = re.search(r"(\d+) passed", self.output)
        return int(m.group(1)) if m else 0

    @property
    def summary(self) -> str:
        last = [l for l in self.output.strip().splitlines() if l.strip()][-1:] or ["(no output)"]
        return last[0]


FAILING_LINE = re.compile(r"^(FAILED|ERROR) (\S+)")


def parse_pytest(exit_code: int, output: str) -> TestRun:
    failing = set()
    for line in output.splitlines():
        m = FAILING_LINE.match(line)
        if m:
            failing.add(m.group(2))
    collection_error = "error" in output.lower() and ("during collection" in output or "ImportError" in output)
    return TestRun(exit_code, failing, output, collection_error)


class Workspace:
    def __init__(self, root: Path, python: str = "3.12"):
        self.root = Path(root)             # the run directory
        self.repo = self.root / "repo"     # working copy of the target repo
        self.venv = self.root / "venv"
        self.python = python

    @property
    def py(self) -> str:
        return str(self.venv / "bin" / "python")

    @property
    def test_cmd(self) -> str:
        return f"{self.py} -B -m pytest -rA -p no:cacheprovider"  # -rA so entire graph verify can parse per-test results

    # --- setup -------------------------------------------------------------

    def clone(self, source: str, branch: str) -> None:
        src = Path(source).expanduser()
        if src.exists() and not (src / ".git").exists():
            shutil.copytree(src, self.repo, ignore=shutil.ignore_patterns(*SKIP_DIRS))
            self.git("init", "-q")
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "upshift: baseline snapshot")
        else:
            out = sh(["git", "clone", "-q", str(src) if src.exists() else source, str(self.repo)])
            if out.returncode != 0:
                raise RuntimeError(f"git clone failed: {out.stderr.strip()}")
        self.git("checkout", "-q", "-b", branch)

    def git(self, *args: str) -> str:
        out = sh(["git", "-c", "user.name=upshift", "-c", "user.email=upshift@localhost",
                  "-c", "commit.gpgsign=false", *args], cwd=self.repo)
        if out.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
        return out.stdout

    @neatlogs.span(kind="TOOL", tool_name="install")
    def install(self) -> str:
        """Create the venv and install the project plus pytest. Returns a log of what ran."""
        log = []
        if not self.venv.exists():
            out = sh(["uv", "venv", "-q", "--python", self.python, str(self.venv)])
            if out.returncode != 0:
                raise RuntimeError(f"uv venv failed: {out.stderr.strip()}")
        pip = ["uv", "pip", "install", "-q", "--python", self.py]
        for req in self.requirement_files():
            out = sh([*pip, "-r", str(req)], cwd=self.repo)
            log.append(f"{req.name}: exit {out.returncode} {out.stderr.strip()[-500:]}")
            if out.returncode != 0:
                raise RuntimeError(f"installing {req.name} failed:\n{tail(out.stderr, 3000)}")
        if (self.repo / "pyproject.toml").exists() or (self.repo / "setup.py").exists():
            for target in (".[test,dev]", ".[test]", ".[dev]", "."):
                out = sh([*pip, "-e", target], cwd=self.repo)
                if out.returncode == 0:
                    log.append(f"installed -e {target}")
                    break
            else:
                raise RuntimeError(f"installing the project failed:\n{tail(out.stderr, 3000)}")
        sh([*pip, "pytest"])
        return "\n".join(log)

    # --- dependency pins ---------------------------------------------------

    def requirement_files(self) -> list[Path]:
        return sorted(p for p in self.repo.glob("requirements*.txt") if p.is_file())

    def freeze(self) -> str:
        """Exact `name==version` lines for what is installed in the venv."""
        out = sh(["uv", "pip", "freeze", "--python", self.py])
        return "".join(f"{l.strip()}\n" for l in out.stdout.splitlines() if PINNED.match(l.strip()))

    def installed_version(self, package: str) -> str | None:
        out = sh([self.py, "-c", "import importlib.metadata as m, sys; print(m.version(sys.argv[1]))", package])
        return out.stdout.strip() if out.returncode == 0 else None

    def pin_files(self, package: str) -> list[Path]:
        """Files in the repo that declare `package`."""
        found = []
        for path in [*self.requirement_files(), self.repo / "pyproject.toml"]:
            if path.exists() and _find_requirement(path.read_text(), package):
                found.append(path)
        return found

    @neatlogs.span(kind="TOOL", tool_name="set_version")
    def set_version(self, package: str, version: str) -> str:
        """Rewrite the package's pins to the target version and install it."""
        changed = []
        files = self.pin_files(package)
        for path in files:
            text = path.read_text()
            new = _rewrite_requirement(text, package, version)
            if new != text:
                path.write_text(new)
                changed.append(path.name)
        if not files and self.requirement_files():
            # a transitive dependency (e.g. a vulnerable one): declare a floor so the fix lives in the repo
            path = self.requirement_files()[0]
            text = path.read_text()
            path.write_text(text + ("\n" if text and not text.endswith("\n") else "") + f"{package}>={version}\n")
            changed.append(path.name)
        out = sh(["uv", "pip", "install", "-q", "--python", self.py, f"{package}=={version}"], cwd=self.repo)
        if out.returncode != 0:
            raise RuntimeError(f"installing {package}=={version} failed:\n{tail(out.stderr, 4000)}")
        return f"pins updated in {changed or 'no files'}; installed {package}=={version}"

    # --- tests ---------------------------------------------------------------

    @neatlogs.span(kind="TOOL", tool_name="run_tests")
    def run_tests(self, selector: str = "") -> TestRun:
        # -B: no .pyc files, so a same-size edit within the same second is never masked by stale bytecode
        cmd = [self.py, "-B", "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "--no-header"]
        if selector:
            cmd += selector.split()
        try:
            out = sh(cmd, cwd=self.repo, timeout=1200)
        except subprocess.TimeoutExpired:
            return TestRun(-1, {"<timeout>"}, "pytest timed out after 1200s", False)
        return parse_pytest(out.returncode, out.stdout + out.stderr)

    # --- files ---------------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        path = (self.repo / rel).resolve()
        if self.repo.resolve() not in path.parents and path != self.repo.resolve():
            raise ValueError(f"{rel} is outside the repo")
        return path

    def source_files(self):
        for path in self.repo.rglob("*"):
            if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(self.repo).parts):
                yield path


# --- requirement parsing helpers ---------------------------------------------

def _req_name(spec: str) -> str | None:
    try:
        return canonicalize_name(Requirement(spec).name)
    except InvalidRequirement:
        return None


def _find_requirement(text: str, package: str) -> bool:
    target = canonicalize_name(package)
    return any(_req_name(m.group(2)) == target for m in _REQ_STRING.finditer(text)) or any(
        _req_name(line.split("#")[0].strip()) == target for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("#", "-"))
    )


# a quoted requirement string inside pyproject.toml
_REQ_STRING = re.compile(r"""(["'])([A-Za-z0-9][A-Za-z0-9._\-\[\], ]*\s*(?:[<>=!~;].*?)?)\1""")


def _new_spec(old: str, package: str, version: str) -> str:
    req = Requirement(old)
    extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
    pinned = any(s.operator == "==" for s in req.specifier)
    marker = f"; {req.marker}" if req.marker else ""
    return f"{req.name}{extras}{'==' if pinned else '>='}{version}{marker}"


def _rewrite_requirement(text: str, package: str, version: str) -> str:
    target = canonicalize_name(package)
    lines = []
    for line in text.splitlines(keepends=True):
        body, sep, comment = line.partition("#")
        stripped = body.strip()
        if stripped and not stripped.startswith(("-", "[", '"', "'")) and _req_name(stripped) == target:
            newline = "\n" if line.endswith("\n") else ""
            lines.append(_new_spec(stripped, package, version) + (f"  #{comment.rstrip()}" if sep else "") + newline)
            continue
        lines.append(line)
    text = "".join(lines)

    def swap(m: re.Match) -> str:
        spec = m.group(2)
        return f"{m.group(1)}{_new_spec(spec, package, version)}{m.group(1)}" if _req_name(spec) == target else m.group(0)

    return _REQ_STRING.sub(swap, text)


def load_pyproject(repo: Path) -> dict:
    path = repo / "pyproject.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}
