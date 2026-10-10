"""Run state, saved after every step so a crashed or killed run can resume."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / ".upshift" / "runs"
PHASES = ["setup", "baseline", "bump", "research", "fix", "verify", "report", "done"]


@dataclass
class RunState:
    run_id: str
    kind: str              # bump | vuln | python | api
    source: str            # repo path or URL
    package: str
    wanted: str | None     # what the user asked for, e.g. "2"
    python: str = "3.12"
    fresh_research: bool = False   # ignore a cached playbook (for fair v0/v1 comparisons)
    phase: str = "setup"
    base_commit: str = ""
    old: str = ""
    new: str = ""
    baseline_failing: list[str] = field(default_factory=list)
    baseline_passed: int = 0
    after_bump_failing: list[str] = field(default_factory=list)   # new failures right after the bump
    advisories: dict = field(default_factory=dict)  # vuln jobs: {advisory id: {aliases, fix_versions}}
    playbook_path: str = ""
    round: int = 0
    round_log: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def dir(self) -> Path:
        return RUNS / self.run_id

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "state.json").write_text(json.dumps(asdict(self), indent=2))

    def advance(self, phase: str) -> None:
        self.phase = phase
        self.save()

    def reached(self, phase: str) -> bool:
        """True when `phase` already completed in an earlier attempt."""
        return PHASES.index(self.phase) > PHASES.index(phase)

    @classmethod
    def load(cls, run_id: str) -> "RunState":
        return cls(**json.loads((RUNS / run_id / "state.json").read_text()))
