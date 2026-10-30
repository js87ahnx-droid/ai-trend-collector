"""AI 관련 사전 필터 + Claude로 테마 분류·한국어 요약, 테마별 흐름 요약."""
from __future__ import annotations

import json
import os
import re

from .store import THEMES

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
MAX_TO_CLASSIFY = 40
MAX_STORED_TITLES = 400   # 24시간 × 회차당 15건 ≒ 360

# 범용 소스(gn·gnp·hn)는 AI 관련 낌새가 있는 글만 LLM에 보낸다. 나머지 소스는 이미 AI 주제로 좁혀져 있음.
GENERAL_SOURCES = {"gn", "gnp", "hn"}
AI_WORDS = re.compile(
    r"\b(ai|a\.i\.|llms?|gpt[-\w.]*|chatgpt|openai|anthropic|claude|gemini|llama|mistral|deepseek|qwen|"
    r"copilot|cursor|codex|agents?|agentic|mcp|rag|prompts?|transformer|diffusion|neural|inference|"
    r"fine-?tun\w*|embedding\w*|token\w*|model weights|benchmark\w*|hugging ?face|vibe ?coding|"
    r"machine learning|deep learning|reinforcement learning|superintelligence|agi)\b"
    r"|인공지능|에이전트|프롬프트|모델|생성형|챗봇|클로드|챗GPT|챗지피티|제미나이|코딩 ?도구|바이브 ?코딩|머신러닝|딥러닝|LLM",
    re.I,
)
AD_WORDS = re.compile(
    r"수강생|수강 ?신청|강의 ?(판매|오픈|모집)|얼리버드|할인 ?코드|쿠폰|월 ?\d+만 ?원|부업|수익화|패시브 ?인컴|"
    r"\b(coupon|promo code|discount code|affiliate|course launch|enroll now)\b",
    re.I,
)

CLASSIFY_PROMPT = """너는 AI 업계 뉴스 큐레이터다. 아래 게시글 목록을 보고 각 글을 판정하라.

규칙:
- AI와 무관하거나 광고·강의 판매·수익화 홍보글이면 skip=true, theme="none", gist="".
- 아니면 skip=false와 theme, gist.
- theme은 tip | agent | code | model | research | industry 중 하나
  (tip=프롬프트·실전 활용, agent=에이전트·자동화, code=AI 코딩 도구,
   model=모델·출시·벤치마크, research=연구·실험, industry=업계·정책·논쟁)
- gist는 한국어 한 문장, 60자 안팎. 원문 문장을 베끼지 말고 직접 요약.
  읽는 사람이 업무에 바로 쓸 수 있는 포인트가 있으면 그걸 우선.
- 같은 소식(같은 발표·글·실험)을 다룬 글이 목록에 여러 개면 score가 가장 높은 하나만 남기고
  (score가 없으면 먼저 나온 글) 나머지는 skip=true. 소스·언어가 달라도 같은 소식이면 중복이다.
- "이미 저장된 글"과 같은 소식인 글도 skip=true.
- 목록의 모든 id에 대해 정확히 한 번씩 답하라.
"""

THEME_PROMPT = """최근 24시간 동안 수집된 아래 글들의 테마별 흐름을 한국어 한 줄(40자 안팎)씩 요약하라.
글이 없는 테마는 제외. 개별 글 제목을 그대로 옮기지 말고 흐름을 묶어서 말하라.

글(테마별):
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
                    "theme": {"type": "string", "enum": list(THEMES) + ["none"]},
                    "gist": {"type": "string"},
                },
                "required": ["id", "skip", "theme", "gist"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": list(THEMES)},
                    "summary": {"type": "string"},
                },
                "required": ["key", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["themes"],
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


def _ask(client, prompt: str, schema: dict, max_tokens: int) -> dict:
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise LLMError("모델이 요청을 거절함")
    if resp.stop_reason == "max_tokens":
        raise LLMError("max_tokens 도달 — 응답 잘림")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise LLMError("텍스트 응답 없음")
    return json.loads(text)


def classify(cands: list[dict], client, stored_titles: list[str] = ()) -> list[dict]:
    """cands에 theme·gist를 붙여 반환. skip·누락·잘못된 응답은 버린다.
    stored_titles: 최근 저장된 글 제목 — 같은 소식의 재수집을 LLM이 걸러내도록 함께 보낸다."""
    if not cands:
        return []
    stored = "\n".join(f"- {t}" for t in list(stored_titles)[-MAX_STORED_TITLES:]) or "(없음)"
    lines = [
        json.dumps({k: c.get(k) for k in ("id", "title", "source", "score", "comments")} | {"excerpt": (c.get("excerpt") or "")[:300]},
                   ensure_ascii=False)
        for c in cands
    ]
    prompt = f"{CLASSIFY_PROMPT}\n이미 저장된 글(최근 24시간):\n{stored}\n\n게시글(JSON Lines):\n" + "\n".join(lines)
    data = _ask(client, prompt, CLASSIFY_SCHEMA, 16000)
    labels: dict[str, dict] = {}
    for r in data.get("results", []):
        labels.setdefault(r.get("id"), r)   # 같은 id가 두 번 오면 첫 답만
    out = []
    for c in cands:                          # 입력 순서(=우선순위) 유지
        r = labels.get(c["id"])
        gist = ((r or {}).get("gist") or "").strip()
        if not r or r.get("skip") or r.get("theme") not in THEMES or not gist:
            continue
        out.append(c | {"theme": r["theme"], "gist": gist})
    return out


def summarize_themes(items: list[dict], client) -> list[dict]:
    if not items:
        return []
    groups: dict[str, list[str]] = {}
    for it in items:
        groups.setdefault(it["theme"], []).append(f"- {it['title']} — {it['gist']}")
    body = "\n".join(f"[{k}]\n" + "\n".join(v[:25]) for k, v in groups.items())
    data = _ask(client, THEME_PROMPT + body, THEME_SCHEMA, 4000)
    present = set(groups)
    seen, out = set(), []
    for t in data.get("themes", []):
        if t["key"] in present and t["key"] not in seen and t["summary"].strip():
            seen.add(t["key"])
            out.append({"key": t["key"], "summary": t["summary"].strip()})
    return sorted(out, key=lambda t: THEMES.index(t["key"]))
