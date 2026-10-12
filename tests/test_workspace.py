from upshift.workspace import _find_requirement, _rewrite_requirement, parse_pytest


def test_rewrite_pinned_requirement_keeps_pin_style_and_comment():
    text = "requests>=2\npydantic==1.10.13  # models\n"
    assert _rewrite_requirement(text, "pydantic", "2.14.0") == "requests>=2\npydantic==2.14.0  # models\n"


def test_rewrite_keeps_extras_and_markers():
    text = 'pydantic[email]>=1.8,<2; python_version >= "3.8"\n'
    assert _rewrite_requirement(text, "Pydantic", "2.14.0") == \
        'pydantic[email]>=2.14.0; python_version >= "3.8"\n'


def test_rewrite_pyproject_dependency_strings():
    text = '[project]\nname = "shop"\ndependencies = [\n  "pydantic>=1.10,<2",\n  "httpx",\n]\n'
    out = _rewrite_requirement(text, "pydantic", "2.14.0")
    assert '"pydantic>=2.14.0"' in out and '"httpx"' in out and 'name = "shop"' in out


def test_other_packages_untouched():
    text = "pydantic-settings==1.0\npydantic==1.10.13\n"
    assert _rewrite_requirement(text, "pydantic", "2.0") == "pydantic-settings==1.0\npydantic==2.0\n"


def test_find_requirement():
    assert _find_requirement("flask==2.0\n", "Flask")
    assert not _find_requirement("flask-cors==2.0\n", "flask")


def test_parse_pytest_failures_and_collection_errors():
    out = ("FAILED tests/test_a.py::test_one - AssertionError\n"
           "ERROR tests/test_b.py - TypeError: constr() got an unexpected keyword\n"
           "1 failed, 3 passed, 1 error in 0.2s\n")
    run = parse_pytest(1, out)
    assert run.failing == {"tests/test_a.py::test_one", "tests/test_b.py"}
    assert run.passed == 3


def test_fetcher_treats_anchors_as_the_same_page(monkeypatch):
    from upshift import research
    calls = []
    monkeypatch.setattr(research, "fetch_text", lambda url, cap: calls.append(url) or f"page {url}")
    fetch = research.Fetcher(already_read=["https://docs.example.com/changelog/"])
    assert fetch("https://docs.example.com/migration/#config") == "page https://docs.example.com/migration"
    assert "Already fetched" in fetch("https://docs.example.com/migration/#validators")
    assert "Already fetched" in fetch("https://docs.example.com/changelog")
    assert calls == ["https://docs.example.com/migration"] and fetch.duplicates == 2


def test_lock_pins_reads_poetry_and_uv_locks(tmp_path):
    # a project that ships poetry.lock: auditing a fresh install instead of the lock missed 36 vulnerable packages
    from upshift.workspace import lock_pins
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "Requests"\nversion = "2.32.3"\n\n'
        '[[package]]\nname = "torch"\nversion = "2.7.1"\n\n[[package]]\nname = "torch"\nversion = "2.11.0"\n\n'
        '[[package]]\nname = "local-lib"\nversion = "0.1.0"\n[package.source]\ntype = "directory"\nurl = "libs/x"\n')
    assert lock_pins(tmp_path) == ("poetry.lock", {"requests": ["2.32.3"], "torch": ["2.7.1", "2.11.0"]})
    (tmp_path / "poetry.lock").unlink()
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "app"\nversion = "0.1.0"\nsource = { virtual = "." }\n\n'
        '[[package]]\nname = "multidict"\nversion = "6.7.1"\nsource = { registry = "https://pypi.org/simple" }\n')
    assert lock_pins(tmp_path) == ("uv.lock", {"multidict": ["6.7.1"]})
    (tmp_path / "uv.lock").unlink()
    assert lock_pins(tmp_path) == ("", {})
