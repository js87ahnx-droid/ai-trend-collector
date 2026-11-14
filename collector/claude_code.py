"""구독 토큰(CLAUDE_CODE_OAUTH_TOKEN)으로 Claude를 부르는 클라이언트.

구독 토큰은 Messages API에 직접 쓸 수 없어서 Claude Code CLI(`claude -p`)를 거친다.
기존 코드가 쓰던 `client.beta.messages.create(...)` 모양을 그대로 흉내 내므로 분류·상세·다시 쓰기 코드는 바꾸지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

TIMEOUT = 300   # 한 호출 상한 — 회차 전체가 job 시간 제한 안에 끝나게
SERVER_TOOLS = {"web_fetch": "WebFetch"}   # API 서버 도구 → Claude Code 내장 도구
HIDDEN_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
              "CLAUDE_CODE_USE_VERTEX", "SEARCH_API_KEY", "GITHUB_TOKEN", "GH_TOKEN")


class ClaudeCodeError(Exception):
    pass


def build_args(*, model: str, schema: dict | None, tools: list[dict] | None) -> list[str]:
    names = [SERVER_TOOLS[t["name"]] for t in tools or [] if t.get("name") in SERVER_TOOLS]
    uses = sum(t.get("max_uses", 2) for t in tools or [] if t.get("name") in SERVER_TOOLS)
    args = [shutil.which("claude") or "claude", "-p", "--model", model, "--safe-mode", "--no-session-persistence",
            "--permission-prompts", "none", "--output-format", "json",
            "--tools", ",".join(names), "--max-turns", str(uses + 5)]
    if names:
        args += ["--allowedTools", ",".join(names)]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    return args


def env() -> dict:
    """API 키가 남아 있으면 Claude Code가 그 키를 먼저 써서 구독 토큰이 무시되므로 뺀다. 다른 비밀값도 넘기지 않는다."""
    e = {k: v for k, v in os.environ.items() if k not in HIDDEN_ENV}
    if e.get("CLAUDE_CODE_OAUTH_TOKEN"):   # 붙여넣을 때 섞인 줄바꿈·공백 제거
        e["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(e["CLAUDE_CODE_OAUTH_TOKEN"].split())
    return e


def token_shape() -> str:
    """토큰 값은 드러내지 않고 모양만 — 등록 실수(다른 키, 잘린 값) 진단용."""
    raw = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or ""
    clean = "".join(raw.split())
    kind = next((p for p in ("sk-ant-oat", "sk-ant-api", "sk-ant-") if clean.startswith(p)), "알 수 없음")
    return f"종류={kind} 길이={len(clean)} 공백·줄바꿈={'있음' if clean != raw else '없음'}"


def parse(stdout: str, want_json: bool) -> tuple[str, str]:
    """(stop_reason, 본문). 거절·잘림은 본문을 비워 돌려 classify.final_json이 기존 방식대로 알리게 한다."""
    try:
        out = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCodeError(f"Claude Code 출력이 JSON이 아님: {stdout[:200]}") from e
    stop = out.get("stop_reason") or "end_turn"
    if stop in ("refusal", "max_tokens"):
        return stop, ""
    if out.get("is_error"):
        raise ClaudeCodeError(f"Claude Code 오류({out.get('subtype')}): {str(out.get('result'))[:200]}")
    if want_json:
        if out.get("structured_output") is None:
            raise ClaudeCodeError(f"구조화된 답이 없음 ({out.get('subtype')})")
        return stop, json.dumps(out["structured_output"], ensure_ascii=False)
    return stop, out.get("result") or ""


class _Messages:
    def __init__(self, run):
        self._run = run

    def create(self, *, model, messages, max_tokens=None, output_config=None, tools=None, **_ignored):
        """messages는 사용자 메시지 1개만 쓴다(기존 호출이 모두 그렇다). betas·fallbacks 등은 무시."""
        schema = ((output_config or {}).get("format") or {}).get("schema")
        prompt = messages[-1]["content"]
        args = build_args(model=model, schema=schema, tools=tools)
        stop, text = parse(self._run(args, prompt), schema is not None)
        content = [SimpleNamespace(type="text", text=text)] if text else []
        return SimpleNamespace(stop_reason=stop, content=content)


def _run(args: list[str], prompt: str) -> str:
    # 저장소 밖 빈 폴더에서 실행 — 프로젝트 파일·설정을 읽지 않게
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=cwd, env=env(), timeout=TIMEOUT)
    if p.returncode != 0:
        try:
            out = json.loads(p.stdout)
            why = f"{out.get('subtype')}: {out.get('result')}"
        except (json.JSONDecodeError, AttributeError):
            why = p.stdout or p.stderr
        raise ClaudeCodeError(f"claude 실행 실패({p.returncode}): {why[:300]}")
    return p.stdout


class ClaudeCodeClient:
    def __init__(self, run=_run):
        self.beta = SimpleNamespace(messages=_Messages(run))
        self.messages = self.beta.messages
