import json

import pytest

from collector import classify, detail
from collector import claude_code as cc

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}


def fake(stdout):
    calls = []

    def run(args, prompt):
        calls.append((args, prompt))
        return stdout
    return run, calls


def ok(structured=None, result=""):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result, "structured_output": structured})


def create(client, **kw):
    return client.beta.messages.create(model="claude-opus-5-5", max_tokens=100, betas=["x"], fallbacks="default",
                                       messages=[{"role": "user", "content": "프롬프트"}], **kw)


def test_structured_output_becomes_text_block():
    run, calls = fake(ok({"a": 1}))
    resp = create(cc.ClaudeCodeClient(run), output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}})
    assert resp.stop_reason == "end_turn" and classify.final_json(resp) == {"a": 1}
    args, prompt = calls[0]
    assert prompt == "프롬프트"
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[1:3] == ["-p", "--model"] and args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--tools") + 1] == ""          # 도구 없음
    assert "--allowedTools" not in args and "--safe-mode" in args


def test_web_fetch_maps_to_webfetch_only():
    run, calls = fake(ok({"a": 1}))
    create(cc.ClaudeCodeClient(run), output_config={"format": {"schema": SCHEMA}}, tools=[detail.WEB_FETCH])
    args = calls[0][0]
    assert args[args.index("--tools") + 1] == "WebFetch"
    assert args[args.index("--allowedTools") + 1] == "WebFetch"


@pytest.mark.parametrize("stdout", [
    "not json",
    json.dumps({"is_error": True, "subtype": "error_during_execution", "result": "boom"}),
    ok(None),
])
def test_errors_raise(stdout):
    run, _ = fake(stdout)
    with pytest.raises(cc.ClaudeCodeError):
        create(cc.ClaudeCodeClient(run), output_config={"format": {"schema": SCHEMA}})


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_and_truncation_keep_their_meaning(stop):
    run, _ = fake(json.dumps({"is_error": False, "stop_reason": stop, "structured_output": None}))
    resp = create(cc.ClaudeCodeClient(run), output_config={"format": {"schema": SCHEMA}})
    with pytest.raises(classify.LLMError):
        classify.final_json(resp)


def test_api_key_not_passed_to_cli(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setenv("SEARCH_API_KEY", "s")
    e = cc.env()
    assert "ANTHROPIC_API_KEY" not in e and "SEARCH_API_KEY" not in e and e["CLAUDE_CODE_OAUTH_TOKEN"] == "t"


def test_token_wins_over_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    assert isinstance(classify.make_client(), cc.ClaudeCodeClient)
    assert classify.has_credentials()


def test_no_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert not classify.has_credentials()
