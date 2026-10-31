"""글 1건의 상세 — Claude가 web fetch로 원문을 읽고 핵심·따라 하기·보강 설명·그래픽 명세를 쓴다."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from . import classify

log = logging.getLogger("collector")

WORKERS = 4
WEB_FETCH = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 2, "max_content_tokens": 5000}
MAX_CONTINUE = 3

PROMPT = """너는 바쁜 실무자를 위한 AI 큐레이터다. 아래 글의 원문을 web_fetch로 읽고(실패하면 '수집된 본문'만 사용)
독자가 원문을 열지 않고도 바로 써먹을 수 있게 한국어로 정리하라.

규칙:
- 원문 문장을 그대로 옮기지 말고 직접 요약한다. 직접 인용 금지.
- 원문에 없는 사실·숫자를 지어내지 않는다. 확인 못 한 부분은 쓰지 않는다.
- headline: 원문을 읽고 우리 헤드라인이 틀렸거나 과장됐으면 고친 헤드라인(한국어 45자 이내), 맞으면 그대로.
- points: 핵심 3개, 각 한 문장 (80자 이내)
- steps: 독자가 따라 할 단계 3~6개, 각 120자 이내 (명령어·프롬프트·설정은 원문에 있을 때만, `백틱`으로 감싼다).
  따라 할 게 없으면 빈 배열. 인터뷰·인사이트면 '내 일에 적용하는 법'으로.
- extra: 맥락·주의점·대안 등 보강 설명 1~3문장, 300자 이내. 없으면 "".
- figure: 이해를 확실히 돕는 경우에만 하나, 아니면 type="none".
  compare=전/후 또는 A/B 비교(before·after 각 70자 이내, 라벨 각 12자 이내), flow=단계 흐름(steps 2~5개, 각 12자 이내),
  bars=원문에 있는 수치 비교(bars 2~6개, unit), quote=화자의 핵심 주장 요지(text는 직접 인용이 아닌 요지, speaker).
  쓰지 않는 필드는 빈 문자열·빈 배열·0으로.

글:
- 제목: {title}
- 우리 헤드라인: {headline}
- 카테고리: {cat}
- 원문: {url}
- 수집된 본문: {body}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "extra": {"type": "string"},
        "figure": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["none", "compare", "flow", "bars", "quote"]},
                "title": {"type": "string"},
                "before": {"type": "string"},
                "after": {"type": "string"},
                "before_label": {"type": "string"},
                "after_label": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "unit": {"type": "string"},
                "bars": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}, "value": {"type": "number"}},
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "text": {"type": "string"},
                "speaker": {"type": "string"},
            },
            "required": ["type", "title", "before", "after", "before_label", "after_label",
                         "steps", "unit", "bars", "text", "speaker"],
            "additionalProperties": False,
        },
    },
    "required": ["headline", "points", "steps", "extra", "figure"],
    "additionalProperties": False,
}


def write(item: dict, client) -> dict:
    """item에 detail을 붙여 반환. 서버 도구가 pause_turn으로 멈추면 이어서 요청."""
    prompt = PROMPT.format(title=item["title"], headline=item["headline"], cat=item["cat"], url=item["url"],
                           body=(item.get("body") or item.get("excerpt") or "(없음)")[:3000])
    messages = [{"role": "user", "content": prompt}]
    for _ in range(MAX_CONTINUE):
        resp = client.beta.messages.create(
            model=classify.MODEL,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            tools=[WEB_FETCH],
            messages=messages,
        )
        if resp.stop_reason != "pause_turn":
            data = classify.final_json(resp)
            headline = (data.pop("headline", "") or "").strip()
            fixed = {"headline": headline[:60]} if headline else {}
            return item | fixed | {"detail": data}
        messages = messages + [{"role": "assistant", "content": resp.content}]
    raise classify.LLMError("pause_turn 반복 — 상세 작성 중단")


def write_all(items: list[dict], client) -> tuple[list[dict], list[str]]:
    """여러 글을 병렬로. 실패한 글은 빼고 (성공 목록, 실패 사유) 반환 — 다음 회차에 다시 시도된다."""
    def one(it):
        try:
            return write(it, client), None
        except Exception as e:
            log.warning("상세 실패 %s: %s", it["id"], e)
            return None, f"{it['id']}: {type(e).__name__}: {e}"[:200]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, items))
    return [r for r, _ in results if r], [e for _, e in results if e]
