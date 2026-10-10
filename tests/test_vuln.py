from upshift.vuln import fix_version, label, parse_audit
from upshift.workspace import Workspace

REPORT = {"dependencies": [
    {"name": "Jinja2", "version": "2.11.3", "vulns": [
        {"id": "PYSEC-1", "fix_versions": ["3.1.3"], "aliases": ["CVE-2024-22195", "GHSA-x"]},
        {"id": "PYSEC-2", "fix_versions": ["3.1.6"], "aliases": ["CVE-2025-27516"]},
        {"id": "PYSEC-1", "fix_versions": ["3.1.3"], "aliases": ["CVE-2024-22195", "GHSA-x"]},  # pip-audit repeats
    ]},
    {"name": "pydantic", "version": "1.10.13", "vulns": []},
]}


def test_parse_audit_keeps_vulnerable_packages_once():
    findings = parse_audit(REPORT)
    assert list(findings) == ["jinja2"]
    assert list(findings["jinja2"]["advisories"]) == ["PYSEC-1", "PYSEC-2"]


def test_fix_version_is_the_smallest_release_fixing_everything():
    assert fix_version("2.11.3", parse_audit(REPORT)["jinja2"]["advisories"]) == "3.1.6"


def test_fix_version_prefers_the_current_major_line():
    advisories = {"A": {"aliases": [], "fix_versions": ["3.0.1", "2.11.4"]},
                  "B": {"aliases": [], "fix_versions": ["2.11.5"]}}
    assert fix_version("2.11.3", advisories) == "2.11.5"


def test_no_fix_when_an_advisory_has_no_release():
    advisories = {"A": {"aliases": [], "fix_versions": ["2.0"]}, "B": {"aliases": [], "fix_versions": []}}
    assert fix_version("1.0", advisories) is None


def test_label_prefers_the_cve():
    assert label("PYSEC-1", ["GHSA-x", "CVE-2024-22195"]) == "CVE-2024-22195 (PYSEC-1)"
    assert label("GHSA-y", []) == "GHSA-y"


def test_transitive_fix_is_declared_in_requirements(tmp_path, monkeypatch):
    from upshift import workspace
    ws = Workspace(tmp_path)
    ws.repo.mkdir(parents=True)
    (ws.repo / "requirements.txt").write_text("requests==2.31.0")
    monkeypatch.setattr(workspace, "sh", lambda *a, **k: type("Done", (), {"returncode": 0, "stderr": ""})())
    ws.set_version("urllib3", "2.5.0")
    assert (ws.repo / "requirements.txt").read_text() == "requests==2.31.0\nurllib3>=2.5.0\n"
