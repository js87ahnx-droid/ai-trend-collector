"""AI 관련 사전 필터 + Claude로 카테고리·헤드라인 분류, 중복·대체 판정."""
from __future__ import annotations

import json
import os
import re

from .store import CATS

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
MAX_TO_CLASSIFY = 40
MAX_STORED = 300

# 범용 소스는 AI 관련 낌새가 있는 글만 LLM에 보낸다. 나머지 소스는 이미 AI 주제로 좁혀져 있음.
GENERAL_SOURCES = {"gn", "gnp", "hn", "pod", "ph", "nl"}
AI_WORDS = re.compile(
    r"\b(ai|a\.i\.|llms?|gpt[-\w.]*|chatgpt|openai|anthropic|claude|gemini|llama|mistral|deepseek|qwen|"
    r"copilot|cursor|codex|agents?|agentic|mcp|rag|prompts?|transformer|diffusion|neural|inference|"
    r"fine-?tun\w*|embedding\w*|token\w*|model weights|benchmark\w*|hugging ?face|vibe ?coding|evals?|"
    r"machine learning|deep learning|reinforcement learning|superintelligence|agi|automat\w*)\b"
    r"|인공지능|에이전트|프롬프트|모델|생성형|챗봇|클로드|챗GPT|챗지피티|제미나이|코딩 ?도구|바이브 ?코딩|머신러닝|딥러닝|LLM|자동화",
    re.I,
)
AD_WORDS = re.compile(
    r"수강생|수강 ?신청|강의 ?(판매|오픈|모집)|얼리버드|할인 ?코드|쿠폰|월 ?\d+만 ?원|부업|수익화|패시브 ?인컴|"
    r"\b(coupon|promo code|discount code|affiliate|course launch|enroll now)\b",
    re.I,
)

CLASSIFY_PROMPT = """너는 바쁜 실무자를 위한 AI 큐레이터다. 독자는 뉴스보다 '바로 써먹을 것'을 원한다:
실전 팁, 쓸 만한 도구·프로그램, 뜨는 사업 아이템, 권위자·업계 유명인의 인터뷰와 인사이트.

각 게시글을 판정하라.

cat (하나):
- tip: 바로 따라 할 수 있는 활용법·프롬프트·업무 요령
- tool: 써 볼 만한 도구·앱·오픈소스·서비스 (출시, 사용기)
- biz: 사업 아이템·수익 사례·시장 기회
- voice: 권위자·창업자·연구자 인터뷰, 팟캐스트, 에세이 속 인사이트
- shift: 독자가 쓰는 방식이나 비용을 바꾸는 변화만 (요금·한도 변경, 새 모델을 지금 쓸 수 있게 됨, 기능 종료)

skip=true (cat="none"):
- AI와 무관하거나, 광고·강의 판매·수익화 홍보
- 활용 포인트 없는 단순 뉴스: 투자·인수·소송·주가·인사·정책 동향·일반 업계 소식
- 같은 소식을 다룬 글이 목록에 여럿이면 score가 가장 높은 하나만 남기고(없으면 먼저 나온 글) 나머지
- 아래 '저장된 글'과 같은 소식 (단, 더 나은 방법·새 버전이면 skip 대신 replaces)

skip=false이면:
- headline: 한국어 45자 이내. 원제목 번역이 아니라 독자가 얻을 것을 한 줄로 ("~하려면 ~하라", "~를 ~하는 무료 CLI").
  과장·낚시 금지, 숫자가 있으면 살릴 것.
- tag: 10자 이내 꼬리표 (예: "5분", "무료", "오픈소스", "B2B 구독", "팟캐스트", "요금")
- why: 한국어 60자 이내, 왜 지금 볼 만한지
- pick: 0~10, 독자가 오늘 바로 활용할 가치
- replaces: 이 글이 '저장된 글' 중 하나의 팁·도구를 더 나은 방법이나 새 버전으로 대체하면 그 id, 아니면 ""

목록의 모든 id에 대해 정확히 한 번씩 답하라.
"""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "skip": {"type": "boolean"},
                    "cat": {"type": "string", "enum": list(CATS) + ["none"]},
                    "headline": {"type": "string"},
                    "tag": {"type": "string"},
                    "why": {"type": "string"},
                    "pick": {"type": "integer"},
                    "replaces": {"type": "string"},
                },
                "required": ["id", "skip", "cat", "headline", "tag", "why", "pick", "replaces"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


class LLMError(Exception):
    pass


def prefilter(cands: list[dict]) -> list[dict]:
    out = []
    for c in cands:
        text = f"{c['title']} {c.get('excerpt', '')}"
        if AD_WORDS.search(text):
            continue
        if c["source"] in GENERAL_SOURCES and not AI_WORDS.search(c["title"] + " " + c.get("excerpt", "")[:200]):
            continue
        out.append(c)
    return out


def make_client():
    import anthropic

    return anthropic.Anthropic(max_retries=3)


def final_json(resp) -> dict:
    if resp.stop_reason == "refusal":
        raise LLMError("모델이 요청을 거절함")
    if resp.stop_reason == "max_tokens":
        raise LLMError("max_tokens 도달 — 응답 잘림")
    texts = [b.text for b in resp.content if b.type == "text"]
    if not texts:
        raise LLMError(f"텍스트 응답 없음 (stop_reason={resp.stop_reason})")
    return json.loads(texts[-1])


def ask(client, prompt: str, schema: dict, max_tokens: int, **extra) -> dict:
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
        **extra,
    )
    return final_json(resp)


def _clip(s, n: int) -> str:
    return (s or "").strip()[:n]


def classify(cands: list[dict], client, stored: list[dict] = ()) -> tuple[list[dict], list[str]]:
    """(통과한 글, 판정 결과 건너뛴 id). 통과한 글에는 cat·headline·tag·why·pick·replaces를 붙여 pick 높은 순.
    stored: 최근 저장된 글 — 같은 소식 재수집을 거르고 대체 관계를 판정하는 데 쓴다.
    응답에서 빠진 id는 어느 쪽에도 넣지 않는다(다음 회차에 다시 판정)."""
    if not cands:
        return [], []
    live = [s for s in stored if not s.get("superseded_by")][-MAX_STORED:]
    stored_ids = {s["id"] for s in live}
    stored_txt = "\n".join(f"{s['id']} | {s['cat']} | {s['headline']}" for s in live) or "(없음)"
    lines = [
        json.dumps({k: c.get(k) for k in ("id", "title", "source", "score", "comments")}
                   | {"excerpt": (c.get("excerpt") or "")[:300]}, ensure_ascii=False)
        for c in cands
    ]
    prompt = f"{CLASSIFY_PROMPT}\n저장된 글 (id | cat | headline):\n{stored_txt}\n\n게시글(JSON Lines):\n" + "\n".join(lines)
    data = ask(client, prompt, CLASSIFY_SCHEMA, 16000)

    labels: dict[str, dict] = {}
    for r in data.get("results", []):
        labels.setdefault(r.get("id"), r)   # 같은 id가 두 번 오면 첫 답만
    out, skipped = [], []
    for c in cands:
        r = labels.get(c["id"])
        if not r:
            continue
        if r.get("skip") or r.get("cat") not in CATS or not _clip(r.get("headline"), 1):
            skipped.append(c["id"])
            continue
        rep = r.get("replaces") or ""
        out.append(c | {
            "cat": r["cat"],
            "headline": _clip(r["headline"], 60),
            "tag": _clip(r.get("tag"), 14) or "참고",
            "why": _clip(r.get("why"), 90),
            "pick": max(0, min(10, int(r.get("pick") or 0))),
            "replaces": rep if rep in stored_ids else None,
        })
    return sorted(out, key=lambda c: -c["pick"]), skipped   # 안정 정렬 — 같은 점수는 입력 우선순위 유지
