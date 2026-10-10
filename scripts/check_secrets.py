"""Pre-commit guard: refuse commits that stage a .env file or contain an API key.

Install once per clone:  python3 scripts/check_secrets.py --install
It checks the staged diff for (1) any .env file other than .env.example, (2) the actual values of
secret-looking variables from .env and the environment, (3) common key formats.
Matches are reported by file and line, never by value.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)
KEY_FORMATS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),                    # OpenAI-style
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),                # Anthropic
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{24,}"),
]
HOOK = """#!/bin/sh
# upshift: block commits that contain secrets
python3 "$(git rev-parse --show-toplevel)/scripts/check_secrets.py" || exit 1
"""


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def secret_values() -> set[str]:
    values = {v for k, v in os.environ.items() if SECRET_NAME.search(k) and len(v) >= 12}
    env_file = Path(git("rev-parse", "--show-toplevel").strip()) / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if SECRET_NAME.search(key) and len(value) >= 12:
                values.add(value)
    return values


def main() -> int:
    if "--install" in sys.argv:
        hook = Path(git("rev-parse", "--git-path", "hooks").strip()) / "pre-commit"
        hook.write_text(HOOK)
        hook.chmod(0o755)
        print(f"installed {hook}")
        return 0

    problems = []
    for name in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines():
        base = Path(name).name
        if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
            problems.append(f"{name}: .env files must never be committed")

    values = secret_values()
    current = None
    for line in git("diff", "--cached", "-U0", "--no-color").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:]
        if any(v in added for v in values):
            problems.append(f"{current}: contains the value of a secret from your environment or .env")
        elif any(p.search(added) for p in KEY_FORMATS):
            problems.append(f"{current}: looks like an API key")

    if problems:
        print("Commit blocked, possible secrets staged:", file=sys.stderr)
        for p in sorted(set(problems)):
            print(f"  - {p}", file=sys.stderr)
        print("Unstage them (git restore --staged <file>) and keep keys in .env.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
