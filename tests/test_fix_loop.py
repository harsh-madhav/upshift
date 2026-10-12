"""Drive the real fix loop (real repo, venv, pytest, git) with a scripted model instead of Bedrock."""

import itertools

import pytest

from upshift.agent import FixLoop
from upshift.state import RunState
from upshift.workspace import Workspace

PLAYBOOK = {"summary": "test", "rules": [
    {"old": "add(a, b) returns a - b", "new": "return a + b", "detect": "", "why": "bug", "source": "api-diff"}]}


_ids = itertools.count()


def call(name, **args):
    """An assistant message that calls one tool."""
    return {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": f"t{next(_ids)}", "name": name, "input": args}}]}


class ScriptedLLM:
    """Returns one canned assistant message per converse() call."""

    def __init__(self, *messages):
        self.messages = iter(messages)
        self.input_tokens = self.output_tokens = 0

    def converse(self, system, messages, tools=None, max_tokens=8000):
        message = next(self.messages)
        if isinstance(message, Exception):
            raise message
        return {"output": {"message": message}}


@pytest.fixture
def workspace(tmp_path):
    src = tmp_path / "src"
    (src / "tests").mkdir(parents=True)
    (src / "calc.py").write_text("def add(a, b):\n    return a - b\n\n\ndef neg(a):\n    return -a\n")
    (src / "tests" / "test_calc.py").write_text(
        "from calc import add, neg\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_neg():\n    assert neg(2) == -2\n")
    (src / "conftest.py").write_text("")
    ws = Workspace(tmp_path / "run", python="3.12")
    ws.clone(str(src), "upshift/test")
    ws.install()
    return ws


def state_for(ws):
    return RunState(run_id="test", kind="bump", source="x", package="calc", wanted=None,
                    old="1", new="2", baseline_failing=[])


def test_progress_is_committed(workspace):
    llm = ScriptedLLM(
        call("edit_file", path="calc.py", old="return a - b", new="return a + b"),
        call("finish_round", summary="fix add"),
    )
    state = state_for(workspace)
    final = FixLoop(workspace, llm, state, PLAYBOOK, max_rounds=3).run(lambda: None)
    assert not final.failing
    assert state.round == 1
    assert "fix add" in workspace.git("log", "-1", "--format=%s")


def test_spent_budget_stops_and_keeps_the_edits(workspace):
    from upshift.llm import LLMError
    llm = ScriptedLLM(
        call("edit_file", path="calc.py", old="return a - b", new="return a + b"),
        LLMError("UnprocessableEntityError", "Budget has been exceeded! Max budget: 20.0"),
    )
    state = state_for(workspace)
    with pytest.raises(LLMError):
        FixLoop(workspace, llm, state, PLAYBOOK, max_rounds=3).run(lambda: None)
    assert "return a + b" in (workspace.repo / "calc.py").read_text()
    assert state.round == 1 and "edits kept" in state.round_log[-1]


def test_regression_is_reverted(workspace):
    llm = ScriptedLLM(
        # round 1 breaks the passing test and leaves add broken: strictly worse, so it must be reverted
        call("edit_file", path="calc.py", old="return -a", new="return a"),
        call("finish_round", summary="bad change"),
        call("edit_file", path="calc.py", old="return a - b", new="return a + b"),
        call("finish_round", summary="fix add"),
    )
    state = state_for(workspace)
    final = FixLoop(workspace, llm, state, PLAYBOOK, max_rounds=3).run(lambda: None)
    assert "REVERTED" in state.round_log[0]
    assert not final.failing
    assert "return -a" in (workspace.repo / "calc.py").read_text()


def test_bad_edit_goes_back_to_the_model(workspace):
    llm = ScriptedLLM(
        call("edit_file", path="calc.py", old="does not exist", new="x"),
        call("edit_file", path="calc.py", old="return a - b", new="return a + b"),
        call("finish_round", summary="fix add after retry"),
    )
    state = state_for(workspace)
    final = FixLoop(workspace, llm, state, PLAYBOOK, max_rounds=2).run(lambda: None)
    assert not final.failing


def leftovers_in(ws):
    return lambda: [l for l in (ws.repo / "calc.py").read_text().splitlines() if "a - b" in l]


def test_cleanup_migrates_leftovers_and_commits(workspace):
    # tests already pass here once add is fixed; the "deprecated" usage is the old expression
    (workspace.repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\ndef neg(a):\n    return -a\n")
    workspace.git("commit", "-qam", "fix add")
    llm = ScriptedLLM(
        call("edit_file", path="calc.py", old="return a - b", new="return a + (-b)"),
        call("finish_round", summary="migrate sub"),
    )
    state = state_for(workspace)
    FixLoop(workspace, llm, state, PLAYBOOK).cleanup(leftovers_in(workspace), lambda: None)
    assert "a - b" not in (workspace.repo / "calc.py").read_text()
    assert "migrate sub" in workspace.git("log", "-1", "--format=%s")
    assert "1 -> 0 deprecated usages" in state.round_log[-1]


def test_cleanup_that_breaks_tests_is_reverted(workspace):
    (workspace.repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\ndef neg(a):\n    return -a\n")
    workspace.git("commit", "-qam", "fix add")
    llm = ScriptedLLM(
        call("edit_file", path="calc.py", old="return a - b\n\n\ndef neg(a):\n    return -a",
             new="return 0\n\n\ndef neg(a):\n    return a"),
        call("finish_round", summary="bad cleanup"),
        call("finish_round", summary="gave up"),
    )
    state = state_for(workspace)
    FixLoop(workspace, llm, state, PLAYBOOK).cleanup(leftovers_in(workspace), lambda: None)
    assert "return -a" in (workspace.repo / "calc.py").read_text()
    assert "REVERTED" in state.round_log[0]


def test_compact_shortens_old_tool_results_only():
    from upshift.agent import compact
    from upshift.llm import tool_result
    big = "x" * 5000
    messages = [{"role": "user", "content": [{"text": "start"}]}]
    for i in range(4):
        messages.append({"role": "assistant", "content": []})
        messages.append({"role": "user", "content": [tool_result(f"t{i}", big)]})
    compact(messages)
    sizes = [len(m["content"][0]["toolResult"]["content"][0]["text"]) for m in messages[2::2]]
    assert all(size < 1000 for size in sizes[:2]) and sizes[2:] == [5000, 5000]


def test_compact_keeps_the_newest_read_of_each_file():
    # flask-dantic run: with reads shortened after two steps, the model re-read the same three files
    # until both rounds ran out of steps (1.04M input tokens, no edit made)
    from upshift.agent import compact
    from upshift.llm import tool_result
    big = "x" * 5000

    def read(i, path):
        return {"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{i}", "name": "read_file",
                                                              "input": {"path": path}}}]}
    messages = [{"role": "user", "content": [{"text": "start"}]}]
    for i, path in enumerate(["a.py", "b.py", "a.py", "c.py", "d.py", "e.py"]):
        messages.append(read(i, path))
        messages.append({"role": "user", "content": [tool_result(f"t{i}", big)]})
    compact(messages)
    sizes = [len(m["content"][0]["toolResult"]["content"][0]["text"]) for m in messages[2::2]]
    # first a.py read is superseded; b.py, the second a.py and c.py are each the newest read of their file
    assert sizes[0] < 1000 and sizes[1:] == [5000] * 5


def test_failure_digest_drops_warnings_summary():
    from upshift.agent import failure_digest
    out = ("FAILED tests/test_a.py::t - boom\n"
           "=============================== warnings summary ===============================\n"
           "x.py:1: DeprecationWarning: noisy\n" * 3 +
           "-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n"
           "=========================== short test summary info ============================\n"
           "FAILED tests/test_a.py::t - boom\n1 failed, 3 warnings in 0.1s\n")
    digest = failure_digest(out)
    assert "DeprecationWarning" not in digest and "short test summary" in digest and "1 failed" in digest


def test_test_files_cannot_be_edited(workspace):
    from upshift.agent import edit_file
    with pytest.raises(ValueError, match="test file"):
        edit_file(workspace, "tests/test_calc.py", "== 5", "== -1")
    assert "== 5" in (workspace.repo / "tests" / "test_calc.py").read_text()


def test_test_config_cannot_be_edited(workspace):
    # flask-dantic run: with test files refused, the model added a plugin to pytest.ini's addopts
    from upshift.agent import edit_file
    (workspace.repo / "pytest.ini").write_text("[pytest]\naddopts = -q\n")
    with pytest.raises(ValueError, match="test configuration"):
        edit_file(workspace, "pytest.ini", "addopts = -q", "addopts = -q -p calc_compat")


def test_patching_the_upgraded_package_is_refused(workspace):
    # what gpt-5.4 wrote in the flask-dantic run to make tests that use removed v1 APIs pass
    from upshift.agent import edit_file
    patches = [
        "import pydantic.fields as _f\nfrom pydantic.v1.fields import ModelField\n_f.ModelField = ModelField\n",
        "import pydantic\nsetattr(pydantic, 'BaseConfig', dict)\n",
        "from pydantic import fields\nfields.ModelField = object\n",
        "_orig = __import__('builtins').__build_class__\n",
        "import sys\nsys.modules['pydantic.fields'] = object()\n",
    ]
    for new in patches:
        with pytest.raises(ValueError, match="patches code it doesn't own"):
            edit_file(workspace, "calc.py", "def neg(a):", new + "def neg(a):", "pydantic")
    assert "_f.ModelField" not in (workspace.repo / "calc.py").read_text()
    # using the package, and setting attributes on your own objects, is fine
    edit_file(workspace, "calc.py", "def neg(a):",
              "import pydantic\nx = pydantic.VERSION == '2'\nclass C:\n    pass\nC.y = 1\ndef neg(a):", "pydantic")


def test_a_test_file_that_cannot_import_does_not_hide_the_rest(workspace):
    # flask-dantic: two test files import removed v1 APIs; without this the 20 passing tests read as 0
    (workspace.repo / "tests" / "test_broken.py").write_text("import module_removed_in_v2\n")
    run = workspace.run_tests()
    assert run.passed == 1 and "tests/test_broken.py" in run.failing
