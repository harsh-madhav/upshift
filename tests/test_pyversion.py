from upshift.pyversion import detect, retarget
from upshift.research import html_section, python_versions


def test_retarget_moves_every_pin(tmp_path):
    (tmp_path / ".python-version").write_text("3.10.13\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.10-slim\nRUN pip install -r requirements.txt\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "tests.yml").write_text(
        'jobs:\n  test:\n    steps:\n      - uses: actions/setup-python@v5\n        with:\n'
        '          python-version: "3.10"\n      - run: echo 3.10 stays in other lines\n')
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py310\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.8,<3.11"\n[tool.ruff]\ntarget-version = "py310"\n'
        '[tool.mypy]\npython_version = "3.10"\n')
    changed = retarget(tmp_path, "3.10", "3.12")
    assert sorted(changed) == [".github/workflows/tests.yml", ".python-version", "Dockerfile", "pyproject.toml", "tox.ini"]
    assert (tmp_path / ".python-version").read_text() == "3.12\n"
    assert (tmp_path / "Dockerfile").read_text().startswith("FROM python:3.12-slim\n")
    workflow = (tmp_path / ".github" / "workflows" / "tests.yml").read_text()
    assert 'python-version: "3.12"' in workflow and "echo 3.10 stays" in workflow
    assert (tmp_path / "tox.ini").read_text() == "[tox]\nenvlist = py312\n"
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.10"' in pyproject
    assert 'target-version = "py312"' in pyproject and 'python_version = "3.12"' in pyproject


def test_requires_python_that_already_allows_the_new_version_is_kept(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.9"\n')
    assert retarget(tmp_path, "3.10", "3.12") == []
    assert detect(tmp_path) is None


def test_detect_reads_python_version(tmp_path):
    (tmp_path / ".python-version").write_text("3.10.13\n")
    assert detect(tmp_path) == "3.10"


def test_whats_new_pages_between_versions():
    assert python_versions("3.10", "3.12") == ["3.11", "3.12"]


def test_html_section_keeps_nested_sections():
    html = ('<section id="a"><h2>A</h2><section id="removed"><h2>Removed</h2>'
            '<section id="x"><p>gone</p></section></section><section id="b">B</section></section>')
    assert html_section(html, "removed") == ('<section id="removed"><h2>Removed</h2>'
                                             '<section id="x"><p>gone</p></section></section>')
