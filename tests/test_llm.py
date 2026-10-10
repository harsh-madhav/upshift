from types import SimpleNamespace as NS

import pytest

from upshift.llm import LLMConfigError, from_openai, make_llm, openai_tool, to_openai, tool_result, tool_spec


def test_tool_spec_becomes_openai_function():
    spec = tool_spec("read_file", "Read a file.", {"path": {"type": "string"}}, ["path"])
    assert openai_tool(spec) == {"type": "function", "function": {
        "name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}


def test_conversation_round_trip_to_openai():
    messages = [
        {"role": "user", "content": [{"text": "fix it"}]},
        {"role": "assistant", "content": [
            {"text": "reading"},
            {"toolUse": {"toolUseId": "c1", "name": "read_file", "input": {"path": "a.py"}}}]},
        {"role": "user", "content": [tool_result("c1", "no such file", error=True)]},
    ]
    assert to_openai("sys", messages) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "reading", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ERROR: no such file"},
    ]


def test_openai_reply_becomes_tool_use_blocks():
    reply = NS(content=None, tool_calls=[
        NS(id="c2", function=NS(name="edit_file", arguments='{"path": "a.py", "old": "x", "new": "y"}')),
        NS(id="c3", function=NS(name="run_tests", arguments="{not json")),
    ])
    message = from_openai(reply)
    assert message["content"][0]["toolUse"] == {
        "toolUseId": "c2", "name": "edit_file", "input": {"path": "a.py", "old": "x", "new": "y"}}
    assert "_invalid_json" in message["content"][1]["toolUse"]["input"]


def test_provider_must_be_chosen(monkeypatch):
    monkeypatch.delenv("UPSHIFT_PROVIDER", raising=False)
    with pytest.raises(LLMConfigError):
        make_llm()


def test_azure_needs_its_settings(monkeypatch):
    monkeypatch.setenv("UPSHIFT_PROVIDER", "azure-openai")
    monkeypatch.setenv("UPSHIFT_MODEL", "my-deployment")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(LLMConfigError, match="AZURE_OPENAI_API_KEY"):
        make_llm()


def test_playbook_drops_detect_patterns_that_match_new_code():
    from upshift.playbook import sanitize
    pb = sanitize({"rules": [
        {"old": "m.dict()", "new": "m.model_dump()", "detect": r"\.dict\(", "why": "", "source": ""},
        {"old": "assert M(x=1) == {'x': 1}", "new": "assert M(x=1).model_dump() == {'x': 1}",
         "detect": r"==\s*\{", "why": "", "source": ""},
        {"old": "x", "new": "y", "detect": "([", "why": "", "source": ""},
    ]})
    assert [r["detect"] for r in pb["rules"]] == [r"\.dict\(", "", ""]


def test_anthropic_replays_claude_blocks_unchanged():
    from upshift.llm import from_anthropic, to_anthropic
    thinking = NS(type="thinking", thinking="", signature="SIG")
    use = NS(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.py"})
    reply = from_anthropic([thinking, use])
    assert reply["content"] == [{"toolUse": {"toolUseId": "toolu_1", "name": "read_file", "input": {"path": "a.py"}}}]
    messages = [{"role": "user", "content": [{"text": "fix it"}]}, reply,
                {"role": "user", "content": [tool_result("toolu_1", "no such file", error=True), {"text": "go on"}]}]
    out = to_anthropic(messages)
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": "fix it"}]}
    assert out[1]["content"] == [thinking, use]  # thinking blocks must come back exactly as Claude sent them
    assert out[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "no such file", "is_error": True},
        {"type": "text", "text": "go on"}]


def test_anthropic_defaults_to_opus(monkeypatch):
    monkeypatch.setenv("UPSHIFT_PROVIDER", "anthropic")
    monkeypatch.delenv("UPSHIFT_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    llm = make_llm()
    assert llm.model == "claude-opus-5-5" and llm.trims_history_itself


def test_litellm_points_at_the_hackathon_proxy(monkeypatch):
    monkeypatch.setenv("UPSHIFT_PROVIDER", "litellm")
    monkeypatch.setenv("UPSHIFT_MODEL", "claude-sonnet-4.6")
    monkeypatch.setenv("LITELLM_API_KEY", "test")
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    llm = make_llm()
    assert str(llm.client.base_url) == "https://litellm.vision.aivar.app/v1/"
